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
    commit_message: str = "",
    changed_files: list[str] | None = None,
) -> str:
    doc_id = str(uuid.uuid4())
    _get_client().collection(_COLLECTION).document(doc_id).set(
        {
            "source": source,
            "repo_or_doc_id": repo_or_doc_id,
            "trigger_ref": trigger_ref,
            "commit_message": commit_message,
            "changed_files": changed_files or [],
            "risk_level": (report.get("risk_assessment") or {}).get("risk_level"),
            "status": "pending",
            "extracted_context": report.get("extracted_context"),
            "patent_search_results": report.get("patent_search_results"),
            "risk_assessment": report.get("risk_assessment"),
            "final_report": report.get("final_report"),
            "created_at": datetime.datetime.now(datetime.timezone.utc),
        }
    )
    return doc_id


def list_reports(limit: int = 200) -> list[dict]:
    query = (
        _get_client()
        .collection(_COLLECTION)
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    docs = query.stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]


def get_report(report_id: str) -> dict | None:
    doc = _get_client().collection(_COLLECTION).document(report_id).get()
    if not doc.exists:
        return None
    return {"id": doc.id, **doc.to_dict()}


def mark_resolved(report_id: str) -> None:
    _get_client().collection(_COLLECTION).document(report_id).update({"status": "resolved"})


def get_dashboard_stats() -> dict:
    docs = _get_client().collection(_COLLECTION).stream()
    all_reports = [d.to_dict() for d in docs]

    now = datetime.datetime.now(datetime.timezone.utc)
    week_ago = now - datetime.timedelta(days=7)

    repos: set[str] = set()
    total = 0
    pending_count = 0
    risky_this_week = 0

    for r in all_reports:
        total += 1
        if r.get("repo_or_doc_id"):
            repos.add(r["repo_or_doc_id"])
        risk = r.get("risk_level") or "low"
        status = r.get("status") or "pending"
        if status == "pending" and risk in ("medium", "high"):
            pending_count += 1
        created = r.get("created_at")
        if created and risk in ("medium", "high") and created >= week_ago:
            risky_this_week += 1

    return {
        "workspace_count": len(repos),
        "total_reports": total,
        "risky_this_week": risky_this_week,
        "pending_count": pending_count,
    }
