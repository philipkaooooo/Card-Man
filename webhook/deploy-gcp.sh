#!/usr/bin/env bash
# Deploy the webhook to Cloud Run with Firestore and Secret Manager, verify it live, print the URL for eBay.
# usage: ./deploy-gcp.sh PROJECT_ID [REGION]      (never prints a secret)
set -euo pipefail
PROJECT="${1:?project id}"; REGION="${2:-asia-east1}"; SERVICE=cardman-webhook
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; ENVFILE="$ROOT/.env"; umask 077
G="gcloud --project=$PROJECT --quiet"
get() { grep -E "^$1=" "$ENVFILE" | tail -1 | cut -d= -f2- || true; }
setv() { local t; t="$(mktemp)"; { grep -vE "^$1=" "$ENVFILE" || true; printf '%s=%s\n' "$1" "$2"; } > "$t"; mv "$t" "$ENVFILE"; chmod 600 "$ENVFILE"; }
for k in EBAY_CLIENT_ID EBAY_CLIENT_SECRET; do [ -n "$(get $k)" ] || { echo "$k missing from $ENVFILE" >&2; exit 1; }; done
[ -n "$(get EBAY_VERIFICATION_TOKEN)" ] || setv EBAY_VERIFICATION_TOKEN "$(openssl rand -hex 32)"
[ -n "$(get EBAY_DELETIONS_SECRET)" ]   || setv EBAY_DELETIONS_SECRET "$(openssl rand -hex 32)"

$G services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com firestore.googleapis.com secretmanager.googleapis.com
$G firestore databases describe --database='(default)' >/dev/null 2>&1 || $G firestore databases create --location="$REGION" --type=firestore-native
PN="$($G projects describe "$PROJECT" --format='value(projectNumber)')"; SA="$PN-compute@developer.gserviceaccount.com"
for k in EBAY_CLIENT_ID EBAY_CLIENT_SECRET EBAY_VERIFICATION_TOKEN EBAY_DELETIONS_SECRET; do
  $G secrets describe "$k" >/dev/null 2>&1 || $G secrets create "$k" --replication-policy=automatic >/dev/null
  printf '%s' "$(get $k)" | $G secrets versions add "$k" --data-file=- >/dev/null
  $G secrets add-iam-policy-binding "$k" --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor >/dev/null
done
$G projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" --role=roles/datastore.user --condition=None >/dev/null

SECRETS="EBAY_CLIENT_ID=EBAY_CLIENT_ID:latest,EBAY_CLIENT_SECRET=EBAY_CLIENT_SECRET:latest,EBAY_VERIFICATION_TOKEN=EBAY_VERIFICATION_TOKEN:latest,EBAY_DELETIONS_SECRET=EBAY_DELETIONS_SECRET:latest"
deploy() {
  $G run deploy "$SERVICE" --source "$ROOT/webhook" --region "$REGION" --allow-unauthenticated --min-instances 0 --max-instances 2 \
    --memory 256Mi --cpu 1 --concurrency 40 --timeout 30 --set-secrets "$SECRETS" --set-env-vars "QUEUE_BACKEND=firestore,FIRESTORE_PROJECT=$PROJECT,EBAY_ENDPOINT_URL=$1" >/dev/null
}
URL="$(get EBAY_WEBHOOK_URL)"
if [ -z "$URL" ] || ! $G run services describe "$SERVICE" --region "$REGION" >/dev/null 2>&1; then
  deploy "https://pending.invalid/api/ebay/notifications"
  URL="$($G run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')/api/ebay/notifications"
fi
deploy "$URL"
HOST="${URL%/api/ebay/notifications}"
setv EBAY_WEBHOOK_URL "$URL"; setv EBAY_DELETIONS_URL "$HOST/api/ebay/deletions"

CODE="test"; WANT="$(printf '%s%s%s' "$CODE" "$(get EBAY_VERIFICATION_TOKEN)" "$URL" | shasum -a 256 | cut -d' ' -f1)"
GOT=""; for i in $(seq 1 10); do GOT="$(curl -fsS "$URL?challenge_code=$CODE" 2>/dev/null | sed -E 's/.*"challengeResponse":"([0-9a-f]+)".*/\1/' || true)"; [ "$GOT" = "$WANT" ] && break; sleep 3; done
BADSIG="$(printf '{"kid":"x","signature":"AA=="}' | base64)"
P412="$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "X-EBAY-SIGNATURE: $BADSIG" -H 'Content-Type: application/json' "$URL" -d '{"metadata":{"topic":"MARKETPLACE_ACCOUNT_DELETION"}}')"
P401="$(curl -s -o /dev/null -w '%{http_code}' "$HOST/api/ebay/deletions")"
echo "challenge: $([ "$GOT" = "$WANT" ] && echo ok || echo FAILED)  bad signature: $P412 (want 412)  deletions without secret: $P401 (want 401)"
[ "$GOT" = "$WANT" ] && [ "$P412" = 412 ] && [ "$P401" = 401 ] || exit 1
echo "$URL"
