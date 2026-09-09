#!/usr/bin/env bash
set -euo pipefail

: "${GCP_PROJECT:?Set GCP_PROJECT}"
: "${GCP_REGION:?Set GCP_REGION}"
: "${CLOUD_RUN_SERVICE:=fundraising-crm}"
: "${CLOUD_SQL_INSTANCE:=fundraising-crm-db}"
: "${CLOUD_SQL_DATABASE:=fundraising_crm}"
: "${CLOUD_SQL_USER:=fundraising_app}"
: "${ARTIFACT_REPOSITORY:=fundraising-crm}"
: "${DB_PASSWORD_SECRET:=fundraising-crm-db-password}"
: "${CLOUD_RUN_SERVICE_ACCOUNT:=fundraising-crm-runtime@${GCP_PROJECT}.iam.gserviceaccount.com}"
: "${CLOUD_RUN_AUTH_MODE:=iap}"
: "${CRM_ACCESS_PASSWORD_SECRET:=fundraising-crm-access-password}"
: "${CRM_DEFAULT_ACTOR:=Local owner}"

connection_name="${GCP_PROJECT}:${GCP_REGION}:${CLOUD_SQL_INSTANCE}"
image="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${ARTIFACT_REPOSITORY}/fundraising-crm:$(git rev-parse --short HEAD)"
socket="/cloudsql/${connection_name}"
secret_version="$(gcloud secrets versions list "${DB_PASSWORD_SECRET}" \
  --project="${GCP_PROJECT}" --filter='state=ENABLED' --sort-by='~name' --limit=1 \
  --format='value(name)')"
: "${secret_version:?No enabled database-password secret version found}"

service_env="DB_ENGINE=postgres,DB_NAME=${CLOUD_SQL_DATABASE},DB_USER=${CLOUD_SQL_USER},INSTANCE_UNIX_SOCKET=${socket}"
service_secrets="DB_PASSWORD=${DB_PASSWORD_SECRET}:${secret_version}"
case "${CLOUD_RUN_AUTH_MODE}" in
  iap)
    auth_flags=(--no-allow-unauthenticated --iap)
    ;;
  shared_password)
    access_secret_version="$(gcloud secrets versions list "${CRM_ACCESS_PASSWORD_SECRET}" \
      --project="${GCP_PROJECT}" --filter='state=ENABLED' --sort-by='~name' --limit=1 \
      --format='value(name)')"
    : "${access_secret_version:?No enabled access-password secret version found}"
    service_env="${service_env},CRM_AUTH_MODE=shared_password,CRM_DEFAULT_ACTOR=${CRM_DEFAULT_ACTOR}"
    service_secrets="${service_secrets},CRM_ACCESS_PASSWORD=${CRM_ACCESS_PASSWORD_SECRET}:${access_secret_version}"
    auth_flags=(--allow-unauthenticated --no-iap)
    ;;
  *)
    echo "CLOUD_RUN_AUTH_MODE must be iap or shared_password" >&2
    exit 2
    ;;
esac

gcloud builds submit --project="${GCP_PROJECT}" --tag="${image}" .

gcloud run jobs deploy "${CLOUD_RUN_SERVICE}-migrate" \
  --project="${GCP_PROJECT}" \
  --region="${GCP_REGION}" \
  --image="${image}" \
  --service-account="${CLOUD_RUN_SERVICE_ACCOUNT}" \
  --set-cloudsql-instances="${connection_name}" \
  --set-env-vars="DB_ENGINE=postgres,DB_NAME=${CLOUD_SQL_DATABASE},DB_USER=${CLOUD_SQL_USER},INSTANCE_UNIX_SOCKET=${socket}" \
  --set-secrets="DB_PASSWORD=${DB_PASSWORD_SECRET}:${secret_version}" \
  --command=python \
  --args=-m,crm.migrate

gcloud run jobs execute "${CLOUD_RUN_SERVICE}-migrate" \
  --project="${GCP_PROJECT}" \
  --region="${GCP_REGION}" \
  --wait

gcloud run deploy "${CLOUD_RUN_SERVICE}" \
  --project="${GCP_PROJECT}" \
  --region="${GCP_REGION}" \
  --image="${image}" \
  --service-account="${CLOUD_RUN_SERVICE_ACCOUNT}" \
  --add-cloudsql-instances="${connection_name}" \
  --set-env-vars="${service_env}" \
  --set-secrets="${service_secrets}" \
  --port=8080 \
  --cpu=1 \
  --memory=1Gi \
  --concurrency=20 \
  --min=0 \
  --max-instances=3 \
  --timeout=3600 \
  --session-affinity \
  --startup-probe="httpGet.path=/_stcore/health,httpGet.port=8080,initialDelaySeconds=2,failureThreshold=12,timeoutSeconds=2,periodSeconds=5" \
  "${auth_flags[@]}"

gcloud run services describe "${CLOUD_RUN_SERVICE}" \
  --project="${GCP_PROJECT}" \
  --region="${GCP_REGION}" \
  --format='value(status.url)'
