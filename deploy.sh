#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  deploy.sh — Deploy YouTube Content Pipeline to Google Cloud Run
#  Usage: ./deploy.sh [--project PROJECT_ID] [--region REGION]
# ─────────────────────────────────────────────────────────────
set -euo pipefail

# ── Config (override via env or flags) ───────────────────────
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-}"
REGION="${GOOGLE_CLOUD_REGION:-us-central1}"
SERVICE_NAME="content-pipeline-agents"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
MEMORY="2Gi"
CPU="2"
MIN_INSTANCES="0"
MAX_INSTANCES="10"
TIMEOUT="3600"     # 1 hour (for long production jobs)

# ── Parse flags ───────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region)  REGION="$2";    shift 2 ;;
    *)         echo "Unknown flag: $1"; exit 1 ;;
  esac
done

if [[ -z "$PROJECT_ID" ]]; then
  echo "Error: GOOGLE_CLOUD_PROJECT not set."
  echo "Usage: GOOGLE_CLOUD_PROJECT=my-project ./deploy.sh"
  exit 1
fi

echo "═══════════════════════════════════════════════════"
echo "  Deploying: ${SERVICE_NAME}"
echo "  Project:   ${PROJECT_ID}"
echo "  Region:    ${REGION}"
echo "  Image:     ${IMAGE}"
echo "═══════════════════════════════════════════════════"

# ── 1. Enable required APIs ───────────────────────────────────
echo ""
echo "▶ Enabling required GCP APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  --project="${PROJECT_ID}" \
  --quiet

# ── 2. Create Firestore database (if not exists) ──────────────
echo ""
echo "▶ Ensuring Firestore (native mode) exists..."
gcloud firestore databases create \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --quiet 2>/dev/null || echo "   (Firestore already exists — skipping)"

# ── 3. Create GCS bucket for media files ─────────────────────
BUCKET="${PROJECT_ID}-content-pipeline"
echo ""
echo "▶ Ensuring GCS bucket: gs://${BUCKET}..."
gsutil mb -p "${PROJECT_ID}" -l "${REGION}" "gs://${BUCKET}" 2>/dev/null \
  || echo "   (Bucket already exists — skipping)"

# ── 4. Store API keys in Secret Manager ──────────────────────
echo ""
echo "▶ Storing secrets in Secret Manager..."

store_secret() {
  local name=$1
  local value=$2
  if [[ -z "$value" ]]; then
    echo "   Skipping $name (not set)"
    return
  fi
  echo -n "$value" | gcloud secrets create "$name" \
    --project="${PROJECT_ID}" \
    --data-file=- \
    --quiet 2>/dev/null \
    || echo -n "$value" | gcloud secrets versions add "$name" \
       --project="${PROJECT_ID}" \
       --data-file=- \
       --quiet
  echo "   ✓ $name"
}

# Load .env if present
[[ -f .env ]] && set -a && source .env && set +a

store_secret "google-api-key"          "${GOOGLE_API_KEY:-}"
store_secret "tavily-api-key"          "${TAVILY_API_KEY:-}"
store_secret "elevenlabs-api-key"      "${ELEVENLABS_API_KEY:-}"
store_secret "youtube-client-id"       "${YOUTUBE_CLIENT_ID:-}"
store_secret "youtube-client-secret"   "${YOUTUBE_CLIENT_SECRET:-}"
store_secret "youtube-refresh-token"   "${YOUTUBE_REFRESH_TOKEN:-}"
store_secret "calendar-client-id"      "${CALENDAR_CLIENT_ID:-}"
store_secret "calendar-client-secret"  "${CALENDAR_CLIENT_SECRET:-}"
store_secret "calendar-refresh-token"  "${CALENDAR_REFRESH_TOKEN:-}"
store_secret "modal-flux2-url"         "${MODAL_FLUX2_ENDPOINT_URL:-}"
store_secret "modal-token-id"          "${MODAL_TOKEN_ID:-}"
store_secret "modal-token-secret"      "${MODAL_TOKEN_SECRET:-}"
store_secret "firecrawl-api-key"       "${FIRECRAWL_API_KEY:-}"
store_secret "openai-api-key"          "${OPENAI_API_KEY:-}"

# ── 5. Build Docker image ─────────────────────────────────────
echo ""
echo "▶ Building Docker image with Cloud Build..."
gcloud builds submit \
  --tag "${IMAGE}" \
  --project="${PROJECT_ID}" \
  --timeout=600

# ── 6. Deploy to Cloud Run ────────────────────────────────────
echo ""
echo "▶ Deploying to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --platform managed \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --memory "${MEMORY}" \
  --cpu "${CPU}" \
  --min-instances "${MIN_INSTANCES}" \
  --max-instances "${MAX_INSTANCES}" \
  --timeout "${TIMEOUT}" \
  --set-env-vars "\
GOOGLE_CLOUD_PROJECT=${PROJECT_ID},\
GOOGLE_CLOUD_REGION=${REGION},\
GCS_BUCKET=${BUCKET},\
DEMO_MODE=false,\
IMAGE_PROVIDER=${IMAGE_PROVIDER:-imagen},\
LLM_PROVIDER=${LLM_PROVIDER:-gemini},\
GEMINI_MODEL=${GEMINI_MODEL:-gemini-3-flash-preview},\
OPENAI_API_BASE=${OPENAI_API_BASE:-},\
OPENAI_MODEL=${OPENAI_MODEL:-},\
GOOGLE_GENAI_USE_VERTEXAI=${GOOGLE_GENAI_USE_VERTEXAI:-false}" \
  --set-secrets "\
GOOGLE_API_KEY=google-api-key:latest,\
TAVILY_API_KEY=tavily-api-key:latest,\
ELEVENLABS_API_KEY=elevenlabs-api-key:latest,\
YOUTUBE_CLIENT_ID=youtube-client-id:latest,\
YOUTUBE_CLIENT_SECRET=youtube-client-secret:latest,\
YOUTUBE_REFRESH_TOKEN=youtube-refresh-token:latest,\
CALENDAR_CLIENT_ID=calendar-client-id:latest,\
CALENDAR_CLIENT_SECRET=calendar-client-secret:latest,\
CALENDAR_REFRESH_TOKEN=calendar-refresh-token:latest,\
MODAL_FLUX2_ENDPOINT_URL=modal-flux2-url:latest,\
MODAL_TOKEN_ID=modal-token-id:latest,\
MODAL_TOKEN_SECRET=modal-token-secret:latest,\
FIRECRAWL_API_KEY=firecrawl-api-key:latest,\
OPENAI_API_KEY=openai-api-key:latest" \
  --allow-unauthenticated \
  --quiet

# ── 7. Get service URL ────────────────────────────────────────
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
  --platform managed \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --format "value(status.url)")

echo ""
echo "═══════════════════════════════════════════════════"
echo "  ✅ Deployment complete!"
echo "  Service URL: ${SERVICE_URL}"
echo "═══════════════════════════════════════════════════"
echo ""
echo "  Test the deployment:"
echo "  curl ${SERVICE_URL}/health"
echo ""
echo "  Run the full pipeline:"
echo "  curl -X POST ${SERVICE_URL}/pipeline \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"request\": \"Create a YouTube Short about AI trends\", \"niche\": \"tech\"}'"
echo ""

# ── 8. Deploy Analytics Cloud Run Job ────────────────────────
echo "▶ Deploying Analytics Agent as Cloud Run Job..."
gcloud run jobs create analytics-agent \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --memory "1Gi" \
  --task-timeout "300" \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID},RUN_MODE=analytics_job" \
  --set-secrets "GOOGLE_API_KEY=google-api-key:latest,YOUTUBE_REFRESH_TOKEN=youtube-refresh-token:latest" \
  --quiet 2>/dev/null || echo "   (Analytics job already exists — run 'gcloud run jobs update' to update)"

# ── 9. Schedule Analytics Job with Cloud Scheduler ───────────
SA_EMAIL="${SERVICE_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
echo ""
echo "▶ Setting up daily analytics cron (10am UTC)..."
gcloud scheduler jobs create http analytics-daily-cron \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --schedule="0 10 * * *" \
  --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/analytics-agent:run" \
  --message-body='{"niche": "tech"}' \
  --oauth-service-account-email="${SA_EMAIL}" \
  --quiet 2>/dev/null || echo "   (Scheduler job already exists — skipping)"

echo ""
echo "All done! 🚀"
