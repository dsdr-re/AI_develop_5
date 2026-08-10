"""리포트 이력을 Firestore에 저장/조회한다."""

from __future__ import annotations

import datetime
import os
import uuid

from google.cloud import firestore

_COLLECTION = os.environ.get("FIRESTORE_COLLECTION", "ip_sentinel_reports")
_client: firestore.Client | None = None


def _get_client() -> firestore.Client:
    global _client
    if _client is None:
        _client = firestore.Client()
    return _client


def save_report(
    *,
    source: str,
    repo_or_doc_id: str,
    trigger_ref: str,
    report: dict,
) -> str:
    doc_id = str(uuid.uuid4())
    _get_client().collection(_COLLECTION).document(doc_id).set(
        {
            "source": source,
            "repo_or_doc_id": repo_or_doc_id,
            "trigger_ref": trigger_ref,
            "risk_level": (report.get("risk_assessment") or {}).get("risk_level"),
            "extracted_context": report.get("extracted_context"),
            "patent_search_results": report.get("patent_search_results"),
            "risk_assessment": report.get("risk_assessment"),
            "final_report": report.get("final_report"),
            "created_at": datetime.datetime.now(datetime.timezone.utc),
        }
    )
    return doc_id


def list_reports(
    *,
    repo_or_doc_id: str | None = None,
    min_risk_level: str | None = None,
    limit: int = 50,
) -> list[dict]:
    query = _get_client().collection(_COLLECTION)
    if repo_or_doc_id:
        query = query.where("repo_or_doc_id", "==", repo_or_doc_id)
    if min_risk_level:
        query = query.where("risk_level", "==", min_risk_level)
    query = query.order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit)

    docs = query.stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]


def get_report(report_id: str) -> dict | None:
    doc = _get_client().collection(_COLLECTION).document(report_id).get()
    if not doc.exists:
        return None
    return {"id": doc.id, **doc.to_dict()}
