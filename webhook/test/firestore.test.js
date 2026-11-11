// The Firestore queue against a fake Firestore and metadata server, and the Cloud Run server's routing.
import assert from "node:assert/strict";
import { createSign, generateKeyPairSync } from "node:crypto";
import { test } from "node:test";
import { COLLECTION, FirestoreQueue, notificationsHandler } from "../lib/core.js";

function fakeGoogle({ fail = false } = {}) {
  const docs = new Map();
  const calls = { token: 0, project: 0 };
  const f = async (url, init = {}) => {
    const u = new URL(String(url));
    if (u.hostname === "metadata.google.internal") {
      assert.equal(init.headers["Metadata-Flavor"], "Google");
      if (u.pathname.endsWith("/token")) { calls.token++; return Response.json({ access_token: "sa", expires_in: 3600 }); }
      calls.project++; return new Response("proj-1");
    }
    assert.equal(init.headers.Authorization, "Bearer sa");
    if (fail) return new Response("{}", { status: 503 });
    assert.match(u.pathname, new RegExp(`^/v1/projects/proj-1/databases/\\(default\\)/documents/${COLLECTION}`));
    const id = u.pathname.split("/").pop();
    if (init.method === "PATCH") { docs.set(id, JSON.parse(init.body)); return Response.json({}); }
    if (init.method === "DELETE") return docs.delete(id) ? Response.json({}) : new Response("{}", { status: 404 });
    return Response.json({ documents: [...docs.values()] });
  };
  return { f, docs, calls };
}

test("put / list / remove, idempotent per notification, doc ids are hashes", async () => {
  const g = fakeGoogle();
  const q = new FirestoreQueue({ fetch: g.f });
  await q.put("n/1?x", { notificationId: "n/1?x", username: "u" });
  await q.put("n/1?x", { notificationId: "n/1?x", username: "u" });
  await q.put("n2", { notificationId: "n2", username: "v" });
  assert.equal(g.docs.size, 2);
  assert.ok([...g.docs.keys()].every((k) => /^[0-9a-f]{64}$/.test(k)));
  assert.deepEqual((await q.list(500)).map((r) => r.notificationId).sort(), ["n/1?x", "n2"]);
  assert.equal(await q.remove(["n2", "missing"]), 2);
  assert.deepEqual((await q.list(500)).map((r) => r.notificationId), ["n/1?x"]);
  assert.equal(g.calls.token, 1);
  assert.equal(g.calls.project, 1);
});

test("a Firestore outage makes the webhook answer 503 so eBay retries", async () => {
  const { privateKey, publicKey } = generateKeyPairSync("ec", { namedCurve: "P-256" });
  const g = fakeGoogle({ fail: true });
  const f = async (url, init) => {
    const u = String(url);
    if (u.includes("/oauth2/token")) return Response.json({ access_token: "app", expires_in: 7200 });
    if (u.includes("/public_key/")) return Response.json({ algorithm: "ECDSA", digest: "SHA1", key: publicKey.export({ type: "spki", format: "pem" }) });
    return g.f(url, init);
  };
  const env = { EBAY_VERIFICATION_TOKEN: "t".repeat(40), EBAY_ENDPOINT_URL: "https://x.test/api/ebay/notifications", EBAY_CLIENT_ID: "i",
                EBAY_CLIENT_SECRET: "s", QUEUE_BACKEND: "firestore", FIRESTORE_PROJECT: "proj-1" };
  const body = JSON.stringify({ metadata: { topic: "MARKETPLACE_ACCOUNT_DELETION" }, notification: { notificationId: "n", data: { username: "u" } } });
  const s = createSign("sha1"); s.update(body);
  const sig = Buffer.from(JSON.stringify({ kid: "k", signature: s.sign(privateKey, "base64") })).toString("base64");
  let status;
  const res = { writeHead: (c) => { status = c; }, end() {}, setHeader() {} };
  const { Readable } = await import("node:stream");
  const req = Object.assign(Readable.from([Buffer.from(body)]), { method: "POST", url: "/api/ebay/notifications", headers: { "x-ebay-signature": sig } });
  await notificationsHandler({ env, fetch: f, log: { log() {}, warn() {}, error() {} } })(req, res);
  assert.equal(status, 503);
});

test("Cloud Run server routes the three paths and 404s the rest", async () => {
  process.env.NODE_ENV = "test";
  Object.assign(process.env, { EBAY_VERIFICATION_TOKEN: "t".repeat(40), EBAY_ENDPOINT_URL: "https://x.test/api/ebay/notifications" });
  const { server } = await import("../server.js");
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const b = `http://127.0.0.1:${server.address().port}`;
  try {
    assert.equal((await fetch(`${b}/api/ebay/notifications?challenge_code=a`)).status, 200);
    assert.equal((await fetch(`${b}/api/marketplace/notifications?challenge_code=a`)).status, 200);
    assert.equal((await fetch(`${b}/api/ebay/deletions`)).status, 503);   // no secret configured in this test
    assert.equal((await fetch(`${b}/`)).status, 404);
  } finally { server.close(); }
});
