#!/usr/bin/env bash
# Deploys the webhook to Vercel, checks it live, prints the exact URL for eBay. Secrets go to Vercel and ~/card-man/.env only.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENVFILE="$ROOT/.env"
PROJECT="${1:-cardman-webhook}"
cd "$ROOT/webhook"
umask 077
touch "$ENVFILE"; chmod 600 "$ENVFILE"

get() { grep -E "^$1=" "$ENVFILE" | tail -1 | cut -d= -f2- || true; }
setv() {
  local tmp; tmp="$(mktemp)"
  { grep -vE "^$1=" "$ENVFILE" || true; printf '%s=%s\n' "$1" "$2"; } > "$tmp"
  mv "$tmp" "$ENVFILE"; chmod 600 "$ENVFILE"
}
ask() {  # name, prompt
  if [ -z "$(get "$1")" ]; then
    printf '%s: ' "$2" >&2; IFS= read -rs v; echo >&2
    [ -n "$v" ] || { echo "$1 is required" >&2; exit 1; }
    setv "$1" "$v"
  fi
}

for k in EBAY_CLIENT_ID EBAY_CLIENT_SECRET; do
  [ -n "$(get $k)" ] || { echo "$k is not in $ENVFILE: save your eBay keys first" >&2; exit 1; }
done
ask UPSTASH_REDIS_REST_URL "Upstash REST URL (console.upstash.com, your database)"
ask UPSTASH_REDIS_REST_TOKEN "Upstash REST token"
[ -n "$(get EBAY_VERIFICATION_TOKEN)" ] || setv EBAY_VERIFICATION_TOKEN "$(openssl rand -hex 32)"
[ -n "$(get EBAY_DELETIONS_SECRET)" ]   || setv EBAY_DELETIONS_SECRET "$(openssl rand -hex 32)"

V="npx --yes vercel@latest"
$V whoami >/dev/null 2>&1 || $V login
$V link --yes --project "$PROJECT" >/dev/null

putenv() {
  $V env rm "$1" production --yes >/dev/null 2>&1 || true
  printf '%s' "$2" | $V env add "$1" production >/dev/null
}
for k in EBAY_CLIENT_ID EBAY_CLIENT_SECRET EBAY_VERIFICATION_TOKEN EBAY_DELETIONS_SECRET UPSTASH_REDIS_REST_URL UPSTASH_REDIS_REST_TOKEN; do
  putenv "$k" "$(get "$k")"
done
putenv NODEJS_HELPERS 0

echo "First deploy, to learn the production address..."
DEPLOY="$($V deploy --prod --yes 2>/dev/null | tail -1)"
HOST="$($V inspect "$DEPLOY" 2>&1 | grep -oE 'https://[a-z0-9.-]+\.vercel\.app' | grep -v "$(printf '%s' "$DEPLOY" | sed -E 's#https://##')" | awk '{ print length, $0 }' | sort -n | head -1 | cut -d' ' -f2)"
[ -n "$HOST" ] || { echo "Could not read the production address from Vercel. Deployment: $DEPLOY" >&2; exit 1; }
URL="$HOST/api/ebay/notifications"
setv EBAY_WEBHOOK_URL "$URL"
setv EBAY_DELETIONS_URL "$HOST/api/ebay/deletions"
putenv EBAY_ENDPOINT_URL "$URL"

echo "Second deploy, with the endpoint URL set..."
$V deploy --prod --yes >/dev/null 2>&1

CODE="selfcheck$(openssl rand -hex 8)"
WANT="$(printf '%s%s%s' "$CODE" "$(get EBAY_VERIFICATION_TOKEN)" "$URL" | shasum -a 256 | cut -d' ' -f1)"
for i in 1 2 3 4 5 6 7 8 9 10; do
  GOT="$(curl -fsS "$URL?challenge_code=$CODE" 2>/dev/null | sed -E 's/.*"challengeResponse":"([0-9a-f]+)".*/\1/' || true)"
  [ "$GOT" = "$WANT" ] && break
  sleep 3
done
if [ "$GOT" != "$WANT" ]; then
  echo "Live self-check FAILED: the endpoint did not return the expected challenge. Do not enter it in eBay yet." >&2
  exit 1
fi
PROBE="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$URL" -d '{}')"
AUTH="$(curl -s -o /dev/null -w '%{http_code}' "$HOST/api/ebay/deletions")"
[ "$PROBE" = 412 ] && [ "$AUTH" = 401 ] || { echo "Self-check FAILED: unsigned POST returned $PROBE (want 412), deletions without a secret returned $AUTH (want 401)." >&2; exit 1; }

echo
echo "Live self-check passed (challenge correct, unsigned POST refused, private route locked)."
echo "Endpoint URL for eBay:"
echo "$URL"
echo
echo "To show the verification token, run:"
echo "grep ^EBAY_VERIFICATION_TOKEN= \"$ENVFILE\" | cut -d= -f2-"
