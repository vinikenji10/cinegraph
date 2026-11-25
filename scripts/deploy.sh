#!/usr/bin/env bash
# Publica o backend (Cloud Run) e o site (Firebase Hosting).
#
# Uso:
#   ALLOWED_EMAILS="a@usp.br,b@gmail.com" ./scripts/deploy.sh
#   ALLOWED_DOMAINS="usp.br" ./scripts/deploy.sh
#
# Quem não estiver na lista (e-mail verificado) recebe 403 na API.
set -euo pipefail

PROJECT="${PROJECT:-cinegraph-ptc3567}"
REGION="us-central1"
SERVICE="cinegraph-api"
SA="cinegraph-api@${PROJECT}.iam.gserviceaccount.com"
ALLOWED_EMAILS="${ALLOWED_EMAILS:-}"
ALLOWED_DOMAINS="${ALLOWED_DOMAINS:-}"
DAILY_LIMIT="${DAILY_LIMIT:-60}"

if [[ -z "$ALLOWED_EMAILS" && -z "$ALLOWED_DOMAINS" ]]; then
  echo "Defina ALLOWED_EMAILS e/ou ALLOWED_DOMAINS (sem isso ninguém consegue usar o site)." >&2
  exit 1
fi
for f in data/processed/cinegraph.db data/processed/embeddings.npy; do
  [[ -f "$f" ]] || { echo "Falta $f. Rode: uv run cinegraph download && uv run cinegraph build-db && uv run cinegraph build-index" >&2; exit 1; }
done

cd "$(dirname "$0")/.."
echo "==> Travando dependências (uv.lock -> requirements.txt)"
uv export --frozen --no-dev --no-emit-project --no-hashes --format requirements-txt > requirements.txt

echo "==> Cloud Run: ${SERVICE} (${REGION})"
gcloud run deploy "$SERVICE" \
  --source . \
  --project "$PROJECT" --region "$REGION" \
  --service-account "$SA" \
  --allow-unauthenticated \
  --memory 2Gi --cpu 1 --concurrency 8 \
  --min-instances 0 --max-instances 2 --timeout 120 \
  --set-env-vars "^@^GOOGLE_CLOUD_PROJECT=${PROJECT}@CINEGRAPH_VECTOR_BACKEND=firestore@CINEGRAPH_ALLOWED_EMAILS=${ALLOWED_EMAILS}@CINEGRAPH_ALLOWED_DOMAINS=${ALLOWED_DOMAINS}@CINEGRAPH_DAILY_LIMIT=${DAILY_LIMIT}" \
  --set-secrets "ANTHROPIC_API_KEY=anthropic-api-key:latest" \
  --quiet

echo "==> Firebase Hosting + regras do Firestore"
firebase deploy --only hosting,firestore --project "$PROJECT"

SITE_ID="$(firebase hosting:sites:list --project "$PROJECT" --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["sites"][0]["name"].split("/")[-1])' 2>/dev/null || echo ciph-ptc3567)"
echo "Site: https://${SITE_ID}.web.app"
