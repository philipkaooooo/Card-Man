// Real HTTP, real ECDSA keys and signatures. eBay and the queue are local fakes.
import assert from "node:assert/strict";
import { createHash, createSign, generateKeyPairSync } from "node:crypto";
import http from "node:http";
import { test } from "node:test";
import { challengeResponse, deletionsHandler, formatPem, notificationsHandler, QUEUE } from "../lib/core.js";

const ENDPOINT = "https://example.test/api/ebay/notifications";
const TOKEN = "t".repeat(40);
const SECRET = "s".repeat(40);
const { publicKey, privateKey } = generateKeyPairSync("ec", { namedCurve: "P-256" });
const PEM = publicKey.export({ type: "spki", format: "pem" });
const ONE_LINE = PEM.replace(/\n/g, "");
const quiet = { log() {}, warn() {}, error() {} };

function fakes({ keyStatus = 200, tokenStatus = 200, queueStatus = 200 } = {}) {
  const hash = new Map();
  const calls = { token: 0, key: 0, queue: 0 };
  const f = async (url, init) => {
    const u = String(url);
    if (u.includes("/oauth2/token")) {
      calls.token++;
      return new Response(JSON.stringify({ access_token: "app", expires_in: 7200 }), { status: tokenStatus });
    }
    if (u.includes("/public_key/")) {
      calls.key++;
      assert.equal(init.headers.Authorization, "Bearer app");
      return new Response(JSON.stringify({ algorithm: "ECDSA", digest: "SHA1", key: ONE_LINE }), { status: keyStatus });
    }
    calls.queue++;
    const [cmd, , ...a] = JSON.parse(init.body);
    if (queueStatus !== 200) return new Response("{}", { status: queueStatus });
    if (cmd === "HSET") { hash.set(a[0], a[1]); return Response.json({ result: 1 }); }
    if (cmd === "HGETALL") return Response.json({ result: [...hash].flat() });
    if (cmd === "HDEL") { let n = 0; for (const k of a) n += hash.delete(k) ? 1 : 0; return Response.json({ result: n }); }
    throw new Error(`unexpected ${cmd}`);
  };
  return { f, hash, calls };
}

const ENV = { EBAY_VERIFICATION_TOKEN: TOKEN, EBAY_ENDPOINT_URL: ENDPOINT, EBAY_CLIENT_ID: "id", EBAY_CLIENT_SECRET: "sec",
              UPSTASH_REDIS_REST_URL: "https://queue.test", UPSTASH_REDIS_REST_TOKEN: "q", EBAY_DELETIONS_SECRET: SECRET };

async function serve(handler, fn) {
  const srv = http.createServer(handler);
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  try { return await fn(`http://127.0.0.1:${srv.address().port}`); }
  finally { srv.close(); }
}

const body = (id = "n1") => JSON.stringify({
  metadata: { topic: "MARKETPLACE_ACCOUNT_DELETION", schemaVersion: "1.0", deprecated: false },
  notification: { notificationId: id, eventDate: "2026-11-06T00:00:00Z", publishDate: "2026-11-06T00:00:01Z", publishAttemptCount: 1,
                  data: { username: "seller_one", userId: "U1", eiasToken: "E1" } } });

function sign(text, { kid = "k1", key = privateKey } = {}) {
  const s = createSign("sha1"); s.update(text);
  return Buffer.from(JSON.stringify({ alg: "ecdsa", kid, signature: s.sign(key, "base64"), digest: "SHA1" })).toString("base64");
}
const post = (base, text, sig) => fetch(`${base}/`, { method: "POST", body: text, headers: sig ? { "X-EBAY-SIGNATURE": sig } : {} });

test("challenge: sha256 of code + token + exact endpoint, as JSON with 200", async () => {
  const { f } = fakes();
  await serve(notificationsHandler({ env: ENV, fetch: f, log: quiet }), async (b) => {
    const r = await fetch(`${b}/x?challenge_code=abc123`, { headers: { Host: "attacker.test" } });
    assert.equal(r.status, 200);
    assert.match(r.headers.get("content-type"), /application\/json/);
    const want = createHash("sha256").update("abc123" + TOKEN + ENDPOINT).digest("hex");
    assert.deepEqual(await r.json(), { challengeResponse: want });
    assert.equal(challengeResponse("abc123", TOKEN, ENDPOINT), want);
    assert.equal((await fetch(`${b}/x`)).status, 400);
  });
});

test("challenge refuses to answer with a bad token or endpoint", async () => {
  for (const env of [{ ...ENV, EBAY_VERIFICATION_TOKEN: "short" }, { ...ENV, EBAY_ENDPOINT_URL: "http://example.test/x" }, { ...ENV, EBAY_ENDPOINT_URL: undefined }]) {
    await serve(notificationsHandler({ env, log: quiet }), async (b) => assert.equal((await fetch(`${b}/?challenge_code=a`)).status, 500));
  }
});

test("valid signature: queued, acknowledged, retries are idempotent, key is cached", async () => {
  const { f, hash, calls } = fakes();
  await serve(notificationsHandler({ env: ENV, fetch: f, log: quiet }), async (b) => {
    const t = body();
    for (let i = 0; i < 2; i++) assert.equal((await post(b, t, sign(t))).status, 200);
  });
  assert.equal(hash.size, 1);
  const rec = JSON.parse(hash.get("n1"));
  assert.deepEqual([rec.username, rec.userId, rec.eiasToken], ["seller_one", "U1", "E1"]);
  assert.equal(calls.key, 1);
  assert.equal(calls.token, 1);
});

test("key cache expires after an hour", async () => {
  const { f, calls } = fakes();
  let clock = 0;
  await serve(notificationsHandler({ env: ENV, fetch: f, log: quiet, now: () => clock }), async (b) => {
    const t = body();
    await post(b, t, sign(t));
    clock = 59 * 60 * 1000; await post(b, t, sign(t));
    assert.equal(calls.key, 1);
    clock = 61 * 60 * 1000; await post(b, t, sign(t));
    assert.equal(calls.key, 2);
  });
});

test("body reformatted in transit still verifies like eBay's SDKs", async () => {
  const { f, hash } = fakes();
  await serve(notificationsHandler({ env: ENV, fetch: f, log: quiet }), async (b) => {
    const compact = body();
    const pretty = JSON.stringify(JSON.parse(compact), null, 2);
    assert.equal((await post(b, pretty, sign(compact))).status, 200);
  });
  assert.equal(hash.size, 1);
});

test("bad signatures get 412 and queue nothing", async () => {
  const { f, hash } = fakes();
  const other = generateKeyPairSync("ec", { namedCurve: "P-256" }).privateKey;
  await serve(notificationsHandler({ env: ENV, fetch: f, log: quiet }), async (b) => {
    const t = body();
    assert.equal((await post(b, t)).status, 412);                                       // missing
    assert.equal((await post(b, t, "not-base64-json")).status, 412);                    // junk
    assert.equal((await post(b, t, sign(t, { key: other }))).status, 412);              // wrong key
    assert.equal((await post(b, t.replace("seller_one", "someone_else"), sign(t))).status, 412);  // tampered
    assert.equal((await post(b, t, sign(t, { kid: "../../evil" }))).status, 412);       // path tricks in kid
  });
  assert.equal(hash.size, 0);
});

test("unknown key id is 412; eBay or queue outage is 503 so eBay retries", async () => {
  const t = body();
  await serve(notificationsHandler({ env: ENV, fetch: fakes({ keyStatus: 404 }).f, log: quiet }), async (b) => assert.equal((await post(b, t, sign(t))).status, 412));
  await serve(notificationsHandler({ env: ENV, fetch: fakes({ keyStatus: 500 }).f, log: quiet }), async (b) => assert.equal((await post(b, t, sign(t))).status, 503));
  await serve(notificationsHandler({ env: ENV, fetch: fakes({ tokenStatus: 401 }).f, log: quiet }), async (b) => assert.equal((await post(b, t, sign(t))).status, 503));
  const q = fakes({ queueStatus: 500 });
  await serve(notificationsHandler({ env: ENV, fetch: q.f, log: quiet }), async (b) => assert.equal((await post(b, t, sign(t))).status, 503));
  assert.equal(q.hash.size, 0);
});

test("never acknowledges without a queue, rejects big bodies and other methods", async () => {
  const { UPSTASH_REDIS_REST_URL, ...noQueue } = ENV;
  await serve(notificationsHandler({ env: noQueue, fetch: fakes().f, log: quiet }), async (b) => {
    const t = body();
    assert.equal((await post(b, t, sign(t))).status, 503);
  });
  await serve(notificationsHandler({ env: ENV, fetch: fakes().f, log: quiet }), async (b) => {
    assert.equal((await fetch(b, { method: "PUT", body: "x" })).status, 405);
    const r = await post(b, "x".repeat(70000), sign("x")).catch(() => ({ status: 413 }));
    assert.equal(r.status, 413);
  });
});

test("verified other topics are acknowledged and not queued; logs hold no identities", async () => {
  const { f, hash } = fakes();
  const lines = [];
  const log = { log: (m) => lines.push(m), warn: (m) => lines.push(m), error: (m) => lines.push(m) };
  await serve(notificationsHandler({ env: ENV, fetch: f, log }), async (b) => {
    const other = JSON.stringify({ metadata: { topic: "OTHER" } });
    assert.equal((await post(b, other, sign(other))).status, 200);
    const t = body();
    await post(b, t, sign(t));
  });
  assert.equal(hash.size, 1);
  assert.ok(!lines.join(" ").match(/seller_one|U1|E1/));
});

test("deletions API: needs the secret, lists, removes only confirmed ids", async () => {
  const { f, hash } = fakes();
  hash.set("a", JSON.stringify({ notificationId: "a", username: "x" }));
  hash.set("b", JSON.stringify({ notificationId: "b", username: "y" }));
  await serve(deletionsHandler({ env: ENV, fetch: f, log: quiet }), async (b) => {
    assert.equal((await fetch(b)).status, 401);
    assert.equal((await fetch(b, { headers: { Authorization: "Bearer wrong" } })).status, 401);
    const auth = { Authorization: `Bearer ${SECRET}` };
    assert.equal((await (await fetch(b, { headers: auth })).json()).deletions.length, 2);
    const r = await fetch(b, { method: "POST", headers: auth, body: JSON.stringify({ processed: ["a"] }) });
    assert.deepEqual(await r.json(), { removed: 1 });
    assert.equal((await fetch(b, { method: "POST", headers: auth, body: "{}" })).status, 400);
  });
  assert.deepEqual([...hash.keys()], ["b"]);
  await serve(deletionsHandler({ env: { ...ENV, EBAY_DELETIONS_SECRET: "short" }, fetch: f, log: quiet }), async (b) =>
    assert.equal((await fetch(b, { headers: { Authorization: "Bearer short" } })).status, 503));
  assert.equal(QUEUE, "ebay:deletions:pending");
});

test("PEM on one line is normalized to one Node can read", () => {
  assert.equal(formatPem(ONE_LINE).trim(), PEM.trim());
});
