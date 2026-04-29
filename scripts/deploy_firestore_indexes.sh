#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Deploy Firestore composite indexes required by the content pipeline.
#
# Usage:
#   bash scripts/deploy_firestore_indexes.sh
#
# Prerequisites:
#   - gcloud CLI installed and authenticated (gcloud auth login)
#   - GOOGLE_CLOUD_PROJECT set in .env or passed as argument
#
# The script reads firestore.indexes.json and creates each composite index
# via `gcloud firestore indexes composite create`. Indexes that already exist
# are skipped automatically (gcloud returns a "duplicate" error which we catch).
#
# Index creation is async — Firestore builds them in the background.
# Run `gcloud firestore indexes composite list --project=<PROJECT>` to check status.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# Load project ID from .env if not already set
if [ -z "${GOOGLE_CLOUD_PROJECT:-}" ]; then
  if [ -f .env ]; then
    GOOGLE_CLOUD_PROJECT=$(grep -E '^GOOGLE_CLOUD_PROJECT=' .env | cut -d'=' -f2 | tr -d '"' | tr -d "'")
  fi
fi

if [ -z "${GOOGLE_CLOUD_PROJECT:-}" ]; then
  echo "ERROR: GOOGLE_CLOUD_PROJECT is not set."
  echo "Set it in .env or export it: export GOOGLE_CLOUD_PROJECT=your-project-id"
  exit 1
fi

# Load Firestore database name (default: "(default)")
FIRESTORE_DB="${FIRESTORE_DATABASE:-(default)}"
if [ -f .env ] && [ "$FIRESTORE_DB" = "(default)" ]; then
  DB_FROM_ENV=$(grep -E '^FIRESTORE_DATABASE=' .env | cut -d'=' -f2 | tr -d '"' | tr -d "'")
  if [ -n "$DB_FROM_ENV" ]; then
    FIRESTORE_DB="$DB_FROM_ENV"
  fi
fi

echo "Project:  $GOOGLE_CLOUD_PROJECT"
echo "Database: $FIRESTORE_DB"
echo ""

# ── Index definitions ─────────────────────────────────────────────────────────
# Each entry: "collection field1:order field2:order"
# order = ascending or descending (gcloud CLI format)

INDEXES=(
  "chat_sessions user_id:ascending last_message_at:descending"
  "chat_messages session_id:ascending created_at:descending"
  "pipeline_jobs user_id:ascending created_at:descending"
  "twitter_content pipeline_job_id:ascending created_at:descending"
  "scripts pipeline_job_id:ascending _saved_at:descending"
  "videos user_id:ascending updated_at:descending"
  "videos status:ascending created_at:descending"
  "topics niche:ascending used_at:descending"
)

CREATED=0
SKIPPED=0
FAILED=0

for entry in "${INDEXES[@]}"; do
  # Parse: collection field1:order field2:order
  read -r COLLECTION F1 F2 <<< "$entry"
  FIELD1_PATH=$(echo "$F1" | cut -d: -f1)
  FIELD1_ORDER=$(echo "$F1" | cut -d: -f2)
  FIELD2_PATH=$(echo "$F2" | cut -d: -f1)
  FIELD2_ORDER=$(echo "$F2" | cut -d: -f2)

  echo -n "Creating index: $COLLECTION ($FIELD1_PATH $FIELD1_ORDER, $FIELD2_PATH $FIELD2_ORDER) ... "

  OUTPUT=$(gcloud firestore indexes composite create \
    --project="$GOOGLE_CLOUD_PROJECT" \
    --database="$FIRESTORE_DB" \
    --collection-group="$COLLECTION" \
    --query-scope=COLLECTION \
    --field-config="field-path=$FIELD1_PATH,order=$FIELD1_ORDER" \
    --field-config="field-path=$FIELD2_PATH,order=$FIELD2_ORDER" \
    2>&1) && RC=$? || RC=$?

  if [ $RC -eq 0 ]; then
    echo "✓ created (building in background)"
    CREATED=$((CREATED + 1))
  elif echo "$OUTPUT" | grep -qi "already exists\|duplicate\|ALREADY_EXISTS"; then
    echo "— already exists, skipped"
    SKIPPED=$((SKIPPED + 1))
  else
    echo "✗ FAILED"
    echo "  $OUTPUT"
    FAILED=$((FAILED + 1))
  fi
done

echo ""
echo "Done: $CREATED created, $SKIPPED already existed, $FAILED failed."
echo ""

if [ $CREATED -gt 0 ]; then
  echo "Indexes are building in the background. Check status with:"
  echo "  gcloud firestore indexes composite list --project=$GOOGLE_CLOUD_PROJECT --database=$FIRESTORE_DB"
  echo ""
  echo "Indexes typically take 1-5 minutes to build. Queries using these indexes"
  echo "will fail with a 'requires an index' error until building completes."
fi
