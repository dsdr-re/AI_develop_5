"""리포트 이력을 Firestore에 저장/조회한다.

UR-05 대응: 투자 실사 요청이 왔을 때 벼락치기 대신 축적된 이력을 그대로 꺼내 쓸 수 있게 함.
"""

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
    """리포트 하나를 저장하고 문서 ID를 반환한다.

    Args:
        source: "github" | "drive"
        repo_or_doc_id: 저장소 full_name 또는 Drive 문서 ID
        trigger_ref: 커밋 SHA, PR 번호, 문서 revision 등 트리거 식별자
        report: pipeline.run_pipeline()의 반환값
    """
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
    """실사 대응용: 저장된 리포트 이력을 필터링해 조회한다 (UR-05)."""
    query = _get_client().collection(_COLLECTION)
    if repo_or_doc_id:
        query = query.where("repo_or_doc_id", "==", repo_or_doc_id)
    if min_risk_level:
        # low < medium < high 순서 필터링이 필요하면 애플리케이션 레벨에서 추가 필터링 권장
        query = query.where("risk_level", "==", min_risk_level)
    query = query.order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit)

    docs = query.stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]
