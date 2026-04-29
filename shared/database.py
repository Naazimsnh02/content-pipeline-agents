"""
Firestore client with in-memory fallback for local dev without GCP credentials.
All agents use this module for state persistence.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from google.cloud.firestore_v1.base_query import FieldFilter

from shared.config import settings

logger = logging.getLogger(__name__)

# ── Firestore or in-memory dict fallback ────────────────────────────────────

_firestore_client = None
_memory_store: dict[str, dict] = {}   # {collection/doc_id: data}


def _get_firestore():
    global _firestore_client
    if _firestore_client is not None:
        return _firestore_client
    if not settings.has_firestore:
        return None
    try:
        from google.cloud import firestore
        _firestore_client = firestore.Client(
            project=settings.google_cloud_project,
            database=settings.firestore_database,
        )
        logger.info("Firestore connected: project=%s", settings.google_cloud_project)
        return _firestore_client
    except Exception as exc:
        logger.warning("Firestore unavailable (%s) — using in-memory store", exc)
        return None


def _mem_key(collection: str, doc_id: str) -> str:
    return f"{collection}/{doc_id}"


# ── Public API ───────────────────────────────────────────────────────────────

def save(collection: str, doc_id: str, data: dict) -> None:
    """Save a document. Creates or overwrites."""
    # Always add timestamps
    data = {**data, "_saved_at": datetime.now(timezone.utc).isoformat()}
    fs = _get_firestore()
    if fs:
        fs.collection(collection).document(doc_id).set(data)
    else:
        _memory_store[_mem_key(collection, doc_id)] = data


def get(collection: str, doc_id: str) -> Optional[dict]:
    """Fetch a single document by ID. Returns None if not found."""
    fs = _get_firestore()
    if fs:
        doc = fs.collection(collection).document(doc_id).get()
        return doc.to_dict() if doc.exists else None
    return _memory_store.get(_mem_key(collection, doc_id))


def query(
    collection: str,
    filters: Optional[list[tuple]] = None,
    order_by: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """
    Query a collection.
    filters: list of (field, op, value) tuples  e.g. [("niche", "==", "tech")]
    """
    fs = _get_firestore()
    if fs:
        ref = fs.collection(collection)
        if filters:
            for field, op, value in filters:
                ref = ref.where(filter=FieldFilter(field, op, value))
        if order_by:
            from google.cloud.firestore_v1 import Query
            ref = ref.order_by(order_by, direction=Query.DESCENDING)
        ref = ref.limit(limit)
        return [doc.to_dict() for doc in ref.stream()]
    else:
        # Simple in-memory filter
        results = []
        prefix = f"{collection}/"
        for key, doc in _memory_store.items():
            if not key.startswith(prefix):
                continue
            match = True
            if filters:
                for field, op, value in filters:
                    doc_val = doc.get(field)
                    if op == "==" and doc_val != value:
                        match = False
                    elif op == "!=" and doc_val == value:
                        match = False
            if match:
                results.append(doc)
        if order_by:
            results.sort(key=lambda d: d.get(order_by, 0), reverse=True)
        return results[:limit]


def update(collection: str, doc_id: str, fields: dict) -> None:
    """Merge-update specific fields in a document."""
    fs = _get_firestore()
    if fs:
        fs.collection(collection).document(doc_id).update(fields)
    else:
        key = _mem_key(collection, doc_id)
        existing = _memory_store.get(key, {})
        _memory_store[key] = {**existing, **fields}


def delete(collection: str, doc_id: str) -> None:
    fs = _get_firestore()
    if fs:
        fs.collection(collection).document(doc_id).delete()
    else:
        _memory_store.pop(_mem_key(collection, doc_id), None)


def get_recent_topic_titles(niche: str, limit: int = 20, days: int = 30) -> list[str]:
    """Helper: return topic titles for a niche covered within the last `days` days (for deduplication)."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    docs = query(
        "topics",
        filters=[("niche", "==", niche), ("used_at", ">=", cutoff)],
        order_by="used_at",
        limit=limit,
    )
    return [d.get("title", "") for d in docs if d.get("title")]


# Expose as a module-level object for easy import
class _DB:
    save = staticmethod(save)
    get = staticmethod(get)
    query = staticmethod(query)
    update = staticmethod(update)
    delete = staticmethod(delete)
    get_recent_topic_titles = staticmethod(get_recent_topic_titles)


db = _DB()
