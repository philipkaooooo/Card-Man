# eBay Marketplace Account Deletion webhook

`GET/POST /api/ebay/notifications` answers eBay's challenge, verifies each notification's ECDSA signature (raw bytes, public key
cached one hour), stores it in a durable queue (Upstash Redis REST), then acknowledges. No queue or an eBay outage returns 503, so eBay
resends instead of a notification being lost. A bad signature returns 412.

`GET/POST /api/ebay/deletions` is private (bearer secret). `cardman/ebay_deletion.py` on the Mac reads the queue, purges every store and raw
file, and only then confirms. The engine does this on each pass; `python3 -m cardman.ebay_deletion sync` does it by hand.

Environment: EBAY_VERIFICATION_TOKEN, EBAY_ENDPOINT_URL (the exact URL entered in eBay), EBAY_CLIENT_ID, EBAY_CLIENT_SECRET,
UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN, EBAY_DELETIONS_SECRET, NODEJS_HELPERS=0.

Deploy: `./deploy.sh` (needs a Vercel login and an Upstash database). It ends with a live self-check and prints the URL.
Tests: `npm test`.

## Google Cloud (Cloud Run + Firestore)

`server.js` serves the three routes on Cloud Run. With `QUEUE_BACKEND=firestore` the queue is the Firestore collection `ebay_deletions`
(document id = SHA-256 of the notification id), reached with the service's own identity, so no key is stored for it.
`./deploy-gcp.sh PROJECT [REGION]` enables the APIs, creates the database if missing, stores the four secrets in Secret Manager,
deploys twice (the second time with EBAY_ENDPOINT_URL set to the real URL), then checks the challenge, a bad signature (412) and
the locked deletions route (401) live before printing the URL.
