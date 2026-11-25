#!/usr/bin/env bash
# Prepara o projeto no Google Cloud para o backend (uma vez só).
# Pré-requisito: plano Blaze ativo no projeto (Cloud Run, Cloud Build e Secret Manager exigem conta de faturamento).
set -euo pipefail

PROJECT="${PROJECT:-cinegraph-ptc3567}"
SA_NAME="cinegraph-api"
SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"

echo "==> Ativando APIs em ${PROJECT}"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com firestore.googleapis.com identitytoolkit.googleapis.com --project "$PROJECT"

echo "==> Conta de serviço do backend (${SA})"
gcloud iam service-accounts describe "$SA" --project "$PROJECT" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SA_NAME" --project "$PROJECT" --display-name "CineGraph API (Cloud Run)"

# Leitura/escrita no Firestore (vetores, histórico e cota diária)
gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:${SA}" \
  --role roles/datastore.user --condition=None >/dev/null

# O build a partir do código-fonte (gcloud run deploy --source) roda com a conta padrão do Compute.
NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "serviceAccount:${NUMBER}-compute@developer.gserviceaccount.com" \
  --role roles/run.builder --condition=None >/dev/null

echo "==> Segredo da API do Claude"
if ! gcloud secrets describe anthropic-api-key --project "$PROJECT" >/dev/null 2>&1; then
  echo "O segredo 'anthropic-api-key' ainda não existe. Crie com (a chave não aparece na tela):"
  echo "  read -s KEY && printf '%s' \"\$KEY\" | gcloud secrets create anthropic-api-key --project $PROJECT --data-file=-"
  exit 1
fi
gcloud secrets add-iam-policy-binding anthropic-api-key --project "$PROJECT" \
  --member "serviceAccount:${SA}" --role roles/secretmanager.secretAccessor >/dev/null
echo "Pronto."
