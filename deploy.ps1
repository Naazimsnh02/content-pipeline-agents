# ─────────────────────────────────────────────────────────────
#  deploy.ps1 — Deploy YouTube Content Pipeline to Google Cloud Run
#  Usage: .\deploy.ps1 [-Project PROJECT_ID] [-Region REGION]
# ─────────────────────────────────────────────────────────────
param(
    [string]$Project = "",
    [string]$Region  = "us-central1"
)

$ErrorActionPreference = "Continue"

# ── Load .env file ────────────────────────────────────────────
$script:envVars = @{}
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#")) {
            $eqIndex = $line.IndexOf("=")
            if ($eqIndex -gt 0) {
                $key   = $line.Substring(0, $eqIndex).Trim()
                $value = $line.Substring($eqIndex + 1).Trim()
                $script:envVars[$key] = $value
            }
        }
    }
}

function Get-EnvVal {
    param([string]$key, [string]$default = "")
    if ($script:envVars.ContainsKey($key) -and $script:envVars[$key]) {
        return $script:envVars[$key]
    }
    $envVal = [System.Environment]::GetEnvironmentVariable($key)
    if ($envVal) { return $envVal }
    return $default
}

# ── Config ────────────────────────────────────────────────────
if (-not $Project) { $Project = Get-EnvVal "GOOGLE_CLOUD_PROJECT" }
if (-not $Project) {
    Write-Host "ERROR: Project ID not set." -ForegroundColor Red
    Write-Host "Usage: .\deploy.ps1 -Project your-project-id"
    exit 1
}

$SERVICE_NAME  = "content-pipeline-agents"
$IMAGE         = "gcr.io/$Project/$SERVICE_NAME"
$MEMORY        = "4Gi"
$CPU           = "2"
$MIN_INSTANCES = "0"
$MAX_INSTANCES = "10"
$TIMEOUT       = "3600"
$BUCKET        = "$Project-content-pipeline"

Write-Host ""
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "  Deploying: $SERVICE_NAME"
Write-Host "  Project:   $Project"
Write-Host "  Region:    $Region"
Write-Host "  Image:     $IMAGE"
Write-Host "=======================================================" -ForegroundColor Cyan

# ── 1. Enable required APIs ───────────────────────────────────
Write-Host ""
Write-Host "[1/9] Enabling required GCP APIs..." -ForegroundColor Yellow
gcloud services enable run.googleapis.com cloudbuild.googleapis.com firestore.googleapis.com storage.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com --project="$Project" --quiet

# ── 2. Create Firestore database (if not exists) ──────────────
Write-Host ""
Write-Host "[2/9] Ensuring Firestore (native mode) exists..." -ForegroundColor Yellow
gcloud firestore databases create --project="$Project" --location="$Region" --quiet 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "   (Firestore already exists - skipping)" }

# ── 3. Create GCS bucket for media files ─────────────────────
Write-Host ""
Write-Host "[3/9] Ensuring GCS bucket: gs://$BUCKET..." -ForegroundColor Yellow
gsutil mb -p "$Project" -l "$Region" "gs://$BUCKET" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "   (Bucket already exists - skipping)" }

# ── 4. Store API keys in Secret Manager ──────────────────────
Write-Host ""
Write-Host "[4/9] Storing secrets in Secret Manager..." -ForegroundColor Yellow

function Store-Secret {
    param([string]$SecretName, [string]$SecretValue)

    if ([string]::IsNullOrWhiteSpace($SecretValue)) {
        Write-Host "   Skipping $SecretName (not set)"
        return
    }

    # Write value to a temp file (avoids PowerShell pipe encoding issues with gcloud)
    $tmpFile = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmpFile, $SecretValue)

    # Try create first
    gcloud secrets create $SecretName --project="$Project" --data-file="$tmpFile" --quiet 2>$null
    if ($LASTEXITCODE -ne 0) {
        # Secret exists, add new version
        gcloud secrets versions add $SecretName --project="$Project" --data-file="$tmpFile" --quiet 2>$null
    }

    Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue
    Write-Host "   + $SecretName" -ForegroundColor Green
}

Store-Secret "google-api-key"          (Get-EnvVal "GOOGLE_API_KEY")
Store-Secret "vertex-api-key"          (Get-EnvVal "VERTEX_API_KEY")
Store-Secret "tavily-api-key"          (Get-EnvVal "TAVILY_API_KEY")
Store-Secret "elevenlabs-api-key"      (Get-EnvVal "ELEVENLABS_API_KEY")
Store-Secret "youtube-client-id"       (Get-EnvVal "YOUTUBE_CLIENT_ID")
Store-Secret "youtube-client-secret"   (Get-EnvVal "YOUTUBE_CLIENT_SECRET")
Store-Secret "youtube-refresh-token"   (Get-EnvVal "YOUTUBE_REFRESH_TOKEN")
Store-Secret "youtube-data-api-key"    (Get-EnvVal "YOUTUBE_DATA_API_KEY")
Store-Secret "calendar-client-id"      (Get-EnvVal "CALENDAR_CLIENT_ID")
Store-Secret "calendar-client-secret"  (Get-EnvVal "CALENDAR_CLIENT_SECRET")
Store-Secret "calendar-refresh-token"  (Get-EnvVal "CALENDAR_REFRESH_TOKEN")
Store-Secret "modal-flux2-url"         (Get-EnvVal "MODAL_FLUX2_ENDPOINT_URL")
Store-Secret "modal-qwen3-tts-url"     (Get-EnvVal "MODAL_QWEN3_TTS_ENDPOINT_URL")
Store-Secret "modal-music-gen-url"     (Get-EnvVal "MODAL_MUSIC_GEN_ENDPOINT_URL")
Store-Secret "modal-token-id"          (Get-EnvVal "MODAL_TOKEN_ID")
Store-Secret "modal-token-secret"      (Get-EnvVal "MODAL_TOKEN_SECRET")
Store-Secret "firecrawl-api-key"       (Get-EnvVal "FIRECRAWL_API_KEY")
Store-Secret "firebase-api-key"        (Get-EnvVal "FIREBASE_API_KEY")
Store-Secret "openai-api-key"          (Get-EnvVal "OPENAI_API_KEY")

# ── 5. Build Docker image ─────────────────────────────────────
Write-Host ""
Write-Host "[5/9] Building Docker image with Cloud Build..." -ForegroundColor Yellow
gcloud builds submit --tag "$IMAGE" --project="$Project" --timeout=1800
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Docker build failed!" -ForegroundColor Red
    exit 1
}

# ── 6. Deploy to Cloud Run ────────────────────────────────────
Write-Host ""
Write-Host "[6/9] Deploying to Cloud Run..." -ForegroundColor Yellow

$envPairs = @(
    "GOOGLE_CLOUD_PROJECT=$Project"
    "GOOGLE_CLOUD_REGION=$Region"
    "GOOGLE_CLOUD_LOCATION=$(Get-EnvVal 'GOOGLE_CLOUD_LOCATION' 'global')"
    "GCS_BUCKET=$BUCKET"
    "DEMO_MODE=false"
    "IMAGE_PROVIDER=$(Get-EnvVal 'IMAGE_PROVIDER' 'gemini')"
    "GEMINI_IMAGE_MODEL=$(Get-EnvVal 'GEMINI_IMAGE_MODEL' 'gemini-2.5-flash-image')"
    "IMAGE_GENERATION_LOCATION=$(Get-EnvVal 'IMAGE_GENERATION_LOCATION' 'us-central1')"
    "LLM_PROVIDER=$(Get-EnvVal 'LLM_PROVIDER' 'gemini')"
    "GEMINI_MODEL=$(Get-EnvVal 'GEMINI_MODEL' 'gemini-3-flash-preview')"
    "OPENAI_API_BASE=$(Get-EnvVal 'OPENAI_API_BASE')"
    "OPENAI_MODEL=$(Get-EnvVal 'OPENAI_MODEL')"
    "GOOGLE_GENAI_USE_VERTEXAI=$(Get-EnvVal 'GOOGLE_GENAI_USE_VERTEXAI' 'true')"
    "ELEVENLABS_VOICE_ID=$(Get-EnvVal 'ELEVENLABS_VOICE_ID' 'JBFqnCBsd6RMkjVDRZzb')"
    "DEFAULT_VOICE=$(Get-EnvVal 'DEFAULT_VOICE' 'en-US-AriaNeural')"
    "FIRESTORE_DATABASE=$(Get-EnvVal 'FIRESTORE_DATABASE' '(default)')"
    "CALENDAR_ID=$(Get-EnvVal 'CALENDAR_ID' 'primary')"
    "APP_NAME=$(Get-EnvVal 'APP_NAME' 'youtube-content-pipeline')"
    "LOG_LEVEL=$(Get-EnvVal 'LOG_LEVEL' 'INFO')"
)
$envVarString = $envPairs -join ","

$secretPairs = @(
    "GOOGLE_API_KEY=google-api-key:latest"
    "VERTEX_API_KEY=vertex-api-key:latest"
    "TAVILY_API_KEY=tavily-api-key:latest"
    "ELEVENLABS_API_KEY=elevenlabs-api-key:latest"
    "YOUTUBE_CLIENT_ID=youtube-client-id:latest"
    "YOUTUBE_CLIENT_SECRET=youtube-client-secret:latest"
    "YOUTUBE_REFRESH_TOKEN=youtube-refresh-token:latest"
    "YOUTUBE_DATA_API_KEY=youtube-data-api-key:latest"
    "CALENDAR_CLIENT_ID=calendar-client-id:latest"
    "CALENDAR_CLIENT_SECRET=calendar-client-secret:latest"
    "CALENDAR_REFRESH_TOKEN=calendar-refresh-token:latest"
    "MODAL_FLUX2_ENDPOINT_URL=modal-flux2-url:latest"
    "MODAL_QWEN3_TTS_ENDPOINT_URL=modal-qwen3-tts-url:latest"
    "MODAL_MUSIC_GEN_ENDPOINT_URL=modal-music-gen-url:latest"
    "MODAL_TOKEN_ID=modal-token-id:latest"
    "MODAL_TOKEN_SECRET=modal-token-secret:latest"
    "FIRECRAWL_API_KEY=firecrawl-api-key:latest"
    "FIREBASE_API_KEY=firebase-api-key:latest"
    "OPENAI_API_KEY=openai-api-key:latest"
)
$secretsString = $secretPairs -join ","

gcloud run deploy $SERVICE_NAME --image "$IMAGE" --platform managed --region "$Region" --project "$Project" --memory "$MEMORY" --cpu "$CPU" --min-instances "$MIN_INSTANCES" --max-instances "$MAX_INSTANCES" --timeout "$TIMEOUT" --set-env-vars "$envVarString" --set-secrets "$secretsString" --allow-unauthenticated --quiet

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Cloud Run deploy failed!" -ForegroundColor Red
    exit 1
}

# ── 7. Get service URL and set APP_BASE_URL ───────────────────
Write-Host ""
Write-Host "[7/9] Getting service URL and setting APP_BASE_URL..." -ForegroundColor Yellow
$SERVICE_URL = (gcloud run services describe $SERVICE_NAME --platform managed --region "$Region" --project "$Project" --format "value(status.url)").Trim()

gcloud run services update $SERVICE_NAME --platform managed --region "$Region" --project "$Project" --update-env-vars "APP_BASE_URL=$SERVICE_URL" --quiet

Write-Host ""
Write-Host "=======================================================" -ForegroundColor Green
Write-Host "  Deployment complete!" -ForegroundColor Green
Write-Host "  Service URL: $SERVICE_URL" -ForegroundColor Green
Write-Host "=======================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Landing page:  $SERVICE_URL/"
Write-Host "  Dashboard:     $SERVICE_URL/app"
Write-Host "  Health check:  $SERVICE_URL/health"
Write-Host ""

# ── 8. Deploy Analytics Cloud Run Job ────────────────────────
Write-Host "[8/9] Deploying Analytics Agent as Cloud Run Job..." -ForegroundColor Yellow
gcloud run jobs create analytics-agent --image "$IMAGE" --region "$Region" --project "$Project" --memory "1Gi" --task-timeout "300" --set-env-vars "GOOGLE_CLOUD_PROJECT=$Project,RUN_MODE=analytics_job" --set-secrets "GOOGLE_API_KEY=google-api-key:latest,YOUTUBE_REFRESH_TOKEN=youtube-refresh-token:latest" --quiet 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "   (Analytics job already exists - run 'gcloud run jobs update' to update)"
}

# ── 9. Schedule Analytics Job with Cloud Scheduler ───────────
$SA_EMAIL = "$SERVICE_NAME@$Project.iam.gserviceaccount.com"
Write-Host ""
Write-Host "[9/9] Setting up daily analytics cron (10am UTC)..." -ForegroundColor Yellow
gcloud scheduler jobs create http analytics-daily-cron --project="$Project" --location="$Region" --schedule="0 10 * * *" --uri="https://$Region-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$Project/jobs/analytics-agent:run" --message-body="{`"niche`": `"tech`"}" --oauth-service-account-email="$SA_EMAIL" --quiet 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "   (Scheduler job already exists - skipping)"
}

Write-Host ""
Write-Host "All done!" -ForegroundColor Green
