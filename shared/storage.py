"""
Google Cloud Storage helpers for persisting generated media files.

Falls back gracefully to local paths when GCS_BUCKET is not configured,
so the app works identically in local dev and on Cloud Run.

Signed URL strategy
-------------------
Generating V4 signed URLs requires RSA signing. When running with user ADC
credentials (``gcloud auth login``) there is no private key available.

We work around this using the **IAM Credentials ``signBlob`` API**, which
lets *any* identity that holds ``roles/iam.serviceAccountTokenCreator`` on a
service account delegate signing to that SA.  Set ``GCS_SERVICE_ACCOUNT`` in
``.env`` to the email of a service account in your project; the code will
obtain ``google.auth.impersonated_credentials.Credentials`` and use those to
sign.  If ``GCS_SERVICE_ACCOUNT`` is not set we try the plain client
credentials (works on Cloud Run / GCE where the default SA has a key).
"""
from __future__ import annotations
import logging
from datetime import timedelta
from pathlib import Path
from typing import Optional

import google.auth
import google.auth.transport.requests

from shared.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_client():
    """Return a GCS client, or None if google-cloud-storage is unavailable."""
    try:
        from google.cloud import storage
        return storage.Client(project=settings.google_cloud_project or None)
    except Exception as exc:
        logger.warning("GCS client unavailable: %s", exc)
        return None


def _get_signing_credentials():
    """
    Return credentials suitable for signing GCS URLs.

    • If ``GCS_SERVICE_ACCOUNT`` is configured, return impersonated
      credentials backed by that SA (works with user ADC tokens).
    • On Cloud Run / Compute Engine, self-impersonate the default SA
      to get signing credentials (the metadata server token alone can't sign).
    • Otherwise return the default ADC credentials as-is.
    """
    sa_email = settings.gcs_service_account

    # On Cloud Run, auto-detect the default SA email if not explicitly set
    if not sa_email:
        try:
            import requests as _req
            resp = _req.get(
                "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email",
                headers={"Metadata-Flavor": "Google"},
                timeout=2,
            )
            if resp.status_code == 200:
                sa_email = resp.text.strip()
                logger.debug("Auto-detected Cloud Run SA: %s", sa_email)
        except Exception:
            pass

    if sa_email:
        try:
            from google.auth import impersonated_credentials

            source_creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            signing_creds = impersonated_credentials.Credentials(
                source_credentials=source_creds,
                target_principal=sa_email,
                target_scopes=["https://www.googleapis.com/auth/devstorage.read_only"],
                lifetime=3600,
            )
            return signing_creds
        except Exception as exc:
            logger.warning(
                "Could not create impersonated credentials for %s: %s. "
                "Falling back to default credentials.",
                sa_email,
                exc,
            )

    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return creds


def _bucket_name() -> str:
    return settings.gcs_bucket


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def upload_file(local_path: str | Path, destination_blob_name: str) -> str:
    """
    Upload a local file to GCS.

    Returns the gs:// URI on success, or the original local path if GCS is
    not configured or the upload fails (graceful fallback).
    """
    local_path = Path(local_path)
    bucket = _bucket_name()

    if not bucket:
        logger.debug("GCS_BUCKET not set — skipping upload, returning local path")
        return str(local_path)

    client = _get_client()
    if client is None:
        return str(local_path)

    try:
        blob = client.bucket(bucket).blob(destination_blob_name)
        blob.upload_from_filename(str(local_path))
        uri = f"gs://{bucket}/{destination_blob_name}"
        logger.info("Uploaded %s → %s", local_path.name, uri)
        return uri
    except Exception as exc:
        logger.error("GCS upload failed for %s: %s", local_path, exc)
        return str(local_path)


def download_file(gcs_uri: str, local_path: str | Path) -> str:
    """
    Download a GCS object to a local path for processing.

    If gcs_uri is already a local path (GCS not configured), returns it as-is.
    Returns the local path string.
    """
    local_path = Path(local_path)

    if not gcs_uri.startswith("gs://"):
        return gcs_uri  # already local

    client = _get_client()
    if client is None:
        return gcs_uri

    try:
        # Parse gs://bucket/blob
        without_scheme = gcs_uri[5:]
        bucket_name, blob_name = without_scheme.split("/", 1)

        local_path.parent.mkdir(parents=True, exist_ok=True)
        client.bucket(bucket_name).blob(blob_name).download_to_filename(str(local_path))
        logger.info("Downloaded %s → %s", gcs_uri, local_path)
        return str(local_path)
    except Exception as exc:
        logger.error("GCS download failed for %s: %s", gcs_uri, exc)
        return gcs_uri


def get_signed_url(gcs_uri: str, expiry_minutes: int = 60) -> Optional[str]:
    """
    Generate a temporary HTTPS signed URL for a GCS object.

    Uses IAM-impersonated credentials when ``GCS_SERVICE_ACCOUNT`` is set so
    that user ADC tokens (``gcloud auth login``) can also sign URLs without
    needing a private key JSON file.

    Returns None if GCS is not configured or signing fails.
    """
    if not gcs_uri.startswith("gs://"):
        return None  # local path — no signed URL

    client = _get_client()
    if client is None:
        return None

    try:
        without_scheme = gcs_uri[5:]
        bucket_name, blob_name = without_scheme.split("/", 1)

        signing_creds = _get_signing_credentials()

        blob = client.bucket(bucket_name).blob(blob_name)
        url = blob.generate_signed_url(
            expiration=timedelta(minutes=expiry_minutes),
            method="GET",
            version="v4",
            credentials=signing_creds,
        )
        logger.info("Signed URL generated for %s (expires in %dm)", gcs_uri, expiry_minutes)
        return url
    except Exception as exc:
        logger.error("Signed URL generation failed for %s: %s", gcs_uri, exc)
        return None


def gcs_object_exists(gcs_uri: str) -> bool:
    """
    Return True if the GCS object exists, False otherwise.
    Always returns True for local paths (no GCS check needed).
    """
    if not gcs_uri.startswith("gs://"):
        return True  # local path — assume it exists

    client = _get_client()
    if client is None:
        return False

    try:
        without_scheme = gcs_uri[5:]
        bucket_name, blob_name = without_scheme.split("/", 1)
        return client.bucket(bucket_name).blob(blob_name).exists()
    except Exception as exc:
        logger.warning("GCS existence check failed for %s: %s", gcs_uri, exc)
        return False


def delete_file(gcs_uri: str) -> bool:
    """
    Delete a GCS object. Returns True on success, False otherwise.
    No-op (returns True) if gcs_uri is a local path.
    """
    if not gcs_uri.startswith("gs://"):
        return True

    client = _get_client()
    if client is None:
        return False

    try:
        without_scheme = gcs_uri[5:]
        bucket_name, blob_name = without_scheme.split("/", 1)
        client.bucket(bucket_name).blob(blob_name).delete()
        logger.info("Deleted GCS object: %s", gcs_uri)
        return True
    except Exception as exc:
        logger.error("GCS delete failed for %s: %s", gcs_uri, exc)
        return False
