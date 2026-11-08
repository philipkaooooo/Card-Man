// eBay Marketplace Account Deletion: challenge, signature check, durable queue. No dependencies.
// Logs carry ids and outcomes only, never a user's identifiers.
import { createHash, createVerify, timingSafeEqual } from "node:crypto";

export const MAX_BODY = 64 * 1024;
export const KEY_TTL_MS = 60 * 60 * 1000;      // eBay recommends caching public keys for about an hour
export const QUEUE = "ebay:deletions:pending";
const TOKEN_RE = /^[A-Za-z0-9_-]{32,80}$/;
const API = "https://api.ebay.com";
const SCOPE = "https://api.ebay.com/oauth/api_scope";
const DIGESTS = { sha1: "sha1", sha256: "sha256" };

export class SignatureError extends Error {}
export class UpstreamError extends Error {}

export function challengeResponse(code, token, endpoint) {
  return createHash("sha256").update(code).update(token).update(endpoint).digest("hex");
}

export function validateConfig(env, needs) {
  const missing = needs.filter((k) => !env[k]);
  if (missing.length) return `missing ${missing.join(", ")}`;
  if (needs.includes("EBAY_VERIFICATION_TOKEN") && !TOKEN_RE.test(env.EBAY_VERIFICATION_TOKEN))
    return "EBAY_VERIFICATION_TOKEN must be 32-80 characters of A-Z a-z 0-9 _ -";
  if (needs.includes("EBAY_ENDPOINT_URL")) {
    let u;
    try { u = new URL(env.EBAY_ENDPOINT_URL); } catch { return "EBAY_ENDPOINT_URL is not a URL"; }
    if (u.protocol !== "https:" || u.search || u.hash) return "EBAY_ENDPOINT_URL must be plain https with no query";
  }
  return null;
}

export function parseSignatureHeader(header) {
  if (!header) throw new SignatureError("no signature header");
  let d;
  try { d = JSON.parse(Buffer.from(header, "base64").toString("utf8")); }
  catch { throw new SignatureError("signature header is not base64 JSON"); }
  if (!d || typeof d.kid !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(d.kid) || typeof d.signature !== "string")
    throw new SignatureError("signature header lacks kid or signature");
  return d;
}

export function formatPem(key) {
  const m = /-----BEGIN PUBLIC KEY-----([\s\S]*?)-----END PUBLIC KEY-----/.exec(key || "");
  if (!m) throw new UpstreamError("public key response has no PEM");
  const body = m[1].replace(/\s+/g, "").match(/.{1,64}/g).join("\n");
  return `-----BEGIN PUBLIC KEY-----\n${body}\n-----END PUBLIC KEY-----\n`;
}

export function verifyBody(raw, sigHeader, keyRecord) {
  const alg = String(keyRecord.algorithm || sigHeader.alg || "").toLowerCase();
  const digest = DIGESTS[String(keyRecord.digest || sigHeader.digest || "").toLowerCase()];
  if (alg !== "ecdsa" || !digest) throw new SignatureError(`unsupported signature scheme ${alg}/${digest}`);
  const pem = formatPem(keyRecord.key);
  const check = (bytes) => {
    const v = createVerify(digest);
    v.update(bytes);
    try { return v.verify(pem, sigHeader.signature, "base64"); } catch { return false; }
  };
  // eBay's SDKs verify the re-serialized JSON; the bytes received are tried first.
  if (check(raw)) return;
  let canonical;
  try { canonical = Buffer.from(JSON.stringify(JSON.parse(raw.toString("utf8")))); } catch { canonical = null; }
  if (canonical && check(canonical)) return;
  throw new SignatureError("signature does not match");
}

export class EbayKeys {
  constructor({ clientId, clientSecret, fetch: f = globalThis.fetch, now = Date.now }) {
    Object.assign(this, { clientId, clientSecret, fetch: f, now, token: null, tokenUntil: 0, keys: new Map() });
  }
  async appToken() {
    if (this.token && this.tokenUntil > this.now() + 60_000) return this.token;
    const basic = Buffer.from(`${this.clientId}:${this.clientSecret}`).toString("base64");
    const r = await this.fetch(`${API}/identity/v1/oauth2/token`, {
      method: "POST",
      headers: { Authorization: `Basic ${basic}`, "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "client_credentials", scope: SCOPE }).toString(),
    });
    if (!r.ok) throw new UpstreamError(`eBay token request failed: HTTP ${r.status}`);
    const d = await r.json();
    this.token = d.access_token;
    this.tokenUntil = this.now() + Number(d.expires_in || 7200) * 1000;
    return this.token;
  }
  async publicKey(kid) {
    const hit = this.keys.get(kid);
    if (hit && hit.until > this.now()) return hit.record;
    const r = await this.fetch(`${API}/commerce/notification/v1/public_key/${encodeURIComponent(kid)}`, {
      headers: { Authorization: `Bearer ${await this.appToken()}`, Accept: "application/json" },
    });
    if (r.status === 400 || r.status === 404) throw new SignatureError(`unknown signing key (HTTP ${r.status})`);
    if (!r.ok) throw new UpstreamError(`eBay public key request failed: HTTP ${r.status}`);
    const record = await r.json();
    this.keys.set(kid, { record, until: this.now() + KEY_TTL_MS });
    return record;
  }
}

// Upstash Redis REST: ["CMD", ...args] posted as JSON.
export class Queue {
  constructor({ url, token, fetch: f = globalThis.fetch }) {
    Object.assign(this, { url: String(url).replace(/\/$/, ""), token, fetch: f });
  }
  async cmd(...args) {
    const r = await this.fetch(this.url, {
      method: "POST",
      headers: { Authorization: `Bearer ${this.token}`, "Content-Type": "application/json" },
      body: JSON.stringify(args),
    });
    if (!r.ok) throw new UpstreamError(`queue returned HTTP ${r.status}`);
    const d = await r.json();
    if (d.error) throw new UpstreamError("queue refused the command");
    return d.result;
  }
  put(id, value) { return this.cmd("HSET", QUEUE, id, JSON.stringify(value)); }
  async list(limit) {
    const flat = (await this.cmd("HGETALL", QUEUE)) || [];
    const out = [];
    for (let i = 0; i + 1 < flat.length && out.length < limit; i += 2) out.push(JSON.parse(flat[i + 1]));
    return out;
  }
  remove(ids) { return ids.length ? this.cmd("HDEL", QUEUE, ...ids) : 0; }
}

// Firestore (Native mode) over REST with the service's own identity from the metadata server: no keys, no SDK.
const METADATA = "http://metadata.google.internal/computeMetadata/v1";
const FIRESTORE = "https://firestore.googleapis.com/v1";
export const COLLECTION = "ebay_deletions";

export class FirestoreQueue {
  constructor({ project, database = "(default)", fetch: f = globalThis.fetch, now = Date.now }) {
    Object.assign(this, { project, database, fetch: f, now, token: null, tokenUntil: 0 });
  }
  async auth() {
    if (this.token && this.tokenUntil > this.now() + 60_000) return this.token;
    const r = await this.fetch(`${METADATA}/instance/service-accounts/default/token`, { headers: { "Metadata-Flavor": "Google" } });
    if (!r.ok) throw new UpstreamError(`metadata token: HTTP ${r.status}`);
    const d = await r.json();
    this.token = d.access_token;
    this.tokenUntil = this.now() + Number(d.expires_in || 300) * 1000;
    return this.token;
  }
  async projectId() {
    if (this.project) return this.project;
    const r = await this.fetch(`${METADATA}/project/project-id`, { headers: { "Metadata-Flavor": "Google" } });
    if (!r.ok) throw new UpstreamError(`metadata project: HTTP ${r.status}`);
    this.project = (await r.text()).trim();
    return this.project;
  }
  async base() {
    return `${FIRESTORE}/projects/${await this.projectId()}/databases/${this.database}/documents/${COLLECTION}`;
  }
  async call(url, init = {}) {
    const r = await this.fetch(url, { ...init, headers: { Authorization: `Bearer ${await this.auth()}`, "Content-Type": "application/json", ...(init.headers || {}) } });
    if (r.status === 404 && init.method === "DELETE") return null;
    if (!r.ok) throw new UpstreamError(`firestore: HTTP ${r.status}`);
    return r.status === 204 ? null : r.json();
  }
  static docId(id) { return createHash("sha256").update(String(id)).digest("hex"); }
  async put(id, value) {
    await this.call(`${await this.base()}/${FirestoreQueue.docId(id)}`, { method: "PATCH", body: JSON.stringify({ fields: { data: { stringValue: JSON.stringify(value) } } }) });
  }
  async list(limit) {
    const d = await this.call(`${await this.base()}?pageSize=${Math.min(limit, 500)}`, { method: "GET" });
    return (d?.documents || []).map((doc) => JSON.parse(doc.fields.data.stringValue));
  }
  async remove(ids) {
    let n = 0;
    for (const id of ids) {
      await this.call(`${await this.base()}/${FirestoreQueue.docId(id)}`, { method: "DELETE" });
      n += 1;
    }
    return n;
  }
}

export function hasQueue(env) {
  return env.QUEUE_BACKEND === "firestore" || Boolean(env.KV_REST_API_URL || env.UPSTASH_REDIS_REST_URL);
}

export function queueFromEnv(env, f) {
  if (env.QUEUE_BACKEND === "firestore") return new FirestoreQueue({ project: env.FIRESTORE_PROJECT || null, database: env.FIRESTORE_DATABASE || "(default)", fetch: f });
  return new Queue({ url: env.KV_REST_API_URL || env.UPSTASH_REDIS_REST_URL, token: env.KV_REST_API_TOKEN || env.UPSTASH_REDIS_REST_TOKEN, fetch: f });
}

export function readBody(req) {
  return new Promise((resolve, reject) => {
    const parts = [];
    let n = 0;
    req.on("data", (c) => {
      n += c.length;
      if (n > MAX_BODY) { reject(Object.assign(new Error("too large"), { tooLarge: true })); req.destroy(); return; }
      parts.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(parts)));
    req.on("error", reject);
  });
}

export function send(res, status, body) {
  const text = body === undefined ? "" : JSON.stringify(body);
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
  res.end(text);
}

export function safeEqual(a, b) {
  const x = createHash("sha256").update(String(a)).digest();
  const y = createHash("sha256").update(String(b)).digest();
  return timingSafeEqual(x, y);
}

const NOTIFY_NEEDS = ["EBAY_VERIFICATION_TOKEN", "EBAY_ENDPOINT_URL"];
const POST_NEEDS = ["EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"];

export function notificationsHandler({ env = process.env, fetch: f = globalThis.fetch, now = Date.now, log = console } = {}) {
  let keys;
  return async function handler(req, res) {
    try {
      const url = new URL(req.url, "https://internal");
      if (req.method === "GET") {
        const bad = validateConfig(env, NOTIFY_NEEDS);
        if (bad) { log.error(`config: ${bad}`); return send(res, 500, { error: "server not configured" }); }
        const code = url.searchParams.get("challenge_code");
        if (!code) return send(res, 400, { error: "challenge_code required" });
        return send(res, 200, { challengeResponse: challengeResponse(code, env.EBAY_VERIFICATION_TOKEN, env.EBAY_ENDPOINT_URL) });
      }
      if (req.method !== "POST") { res.setHeader("Allow", "GET, POST"); return send(res, 405, { error: "method not allowed" }); }

      const bad = validateConfig(env, POST_NEEDS);
      if (bad || !hasQueue(env)) {
        log.error(`config: ${bad || "no queue configured"}`);
        return send(res, 503, { error: "server not configured" });   // eBay retries; nothing is falsely acknowledged
      }
      const raw = await readBody(req);
      const sig = parseSignatureHeader(req.headers["x-ebay-signature"]);
      keys ||= new EbayKeys({ clientId: env.EBAY_CLIENT_ID, clientSecret: env.EBAY_CLIENT_SECRET, fetch: f, now });
      verifyBody(raw, sig, await keys.publicKey(sig.kid));

      let msg;
      try { msg = JSON.parse(raw.toString("utf8")); } catch { return send(res, 400, { error: "invalid JSON" }); }
      if (msg?.metadata?.topic !== "MARKETPLACE_ACCOUNT_DELETION") return send(res, 200, { ignored: true });
      const n = msg.notification;
      const d = n?.data;
      if (!n?.notificationId || !d || !(d.username || d.userId || d.eiasToken)) return send(res, 400, { error: "missing notification fields" });
      // Same id arriving again overwrites itself, so retries are harmless.
      await queueFromEnv(env, f).put(n.notificationId, {
        notificationId: n.notificationId, eventDate: n.eventDate ?? null, username: d.username ?? null, userId: d.userId ?? null,
        eiasToken: d.eiasToken ?? null, receivedAt: new Date(now()).toISOString(),
      });
      log.log(`queued ${n.notificationId}`);
      return send(res, 200, { received: true });
    } catch (x) {
      if (x.tooLarge) return send(res, 413, { error: "too large" });
      if (x instanceof SignatureError) { log.warn(`rejected: ${x.message}`); return send(res, 412, { error: "signature verification failed" }); }
      log.error(`failed: ${x.message}`);
      return send(res, 503, { error: "temporarily unavailable" });   // eBay resends until acknowledged
    }
  };
}

export function deletionsHandler({ env = process.env, fetch: f = globalThis.fetch, log = console } = {}) {
  return async function handler(req, res) {
    try {
      const secret = env.EBAY_DELETIONS_SECRET;
      if (!secret || secret.length < 32) return send(res, 503, { error: "server not configured" });
      const auth = req.headers.authorization || "";
      if (!safeEqual(auth, `Bearer ${secret}`)) return send(res, 401, { error: "unauthorized" });
      const q = queueFromEnv(env, f);
      if (req.method === "GET") return send(res, 200, { deletions: await q.list(500) });
      if (req.method === "POST") {
        const body = JSON.parse((await readBody(req)).toString("utf8"));
        if (!Array.isArray(body.processed) || body.processed.length > 500 || !body.processed.every((x) => typeof x === "string"))
          return send(res, 400, { error: "processed must be a list of ids" });
        return send(res, 200, { removed: await q.remove(body.processed) });
      }
      res.setHeader("Allow", "GET, POST");
      return send(res, 405, { error: "method not allowed" });
    } catch (x) {
      if (x.tooLarge) return send(res, 413, { error: "too large" });
      if (x instanceof SyntaxError) return send(res, 400, { error: "invalid JSON" });
      log.error(`failed: ${x.message}`);
      return send(res, 503, { error: "temporarily unavailable" });
    }
  };
}
