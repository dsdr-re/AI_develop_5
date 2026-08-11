"""리포트 이력을 Firestore에 저장/조회한다.

UR-05 대응: 투자 실사 요청이 왔을 때 벼락치기 대신 축적된 이력을 그대로 꺼내 쓸 수 있게 함.
필터링(위험도/상태)은 Firestore 복합 인덱스가 필요 없도록 이 모듈에서 전체를 가져온 뒤
호출부(main.py)에서 파이썬으로 처리한다. 소규모 프로젝트 스케일이라 충분하다.
"""

from __future__ import annotations

import datetime
import os
import uuid

from google.cloud import firestore

_COLLECTION = os.environ.get("FIRESTORE_COLLECTION", "ip_sentinel_reports")
_CONNECTED_REPOS_COLLECTION = os.environ.get(
    "FIRESTORE_CONNECTED_REPOS_COLLECTION", "ip_sentinel_connected_repos"
)
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
    license_review: list[dict] | None = None,
) -> str:
    """리포트 하나를 저장하고 문서 ID를 반환한다.

    Args:
        source: "github" | "drive"
        repo_or_doc_id: 저장소 full_name 또는 Drive 문서 ID
        trigger_ref: 커밋 SHA, PR 번호, 문서 revision 등 트리거 식별자 (내부 링크용, 화면엔 안 보여줌)
        report: pipeline.run_pipeline()의 반환값
        commit_message: 사람이 직접 쓴 커밋 메시지 (해시 대신 화면에 표시할 용도)
        changed_files: 이 변경사항에서 실제로 바뀐 파일 이름 목록
        license_review: requirements.txt에 새로 추가된 라이브러리의 라이선스 검토 결과 목록
    """
    doc_id = str(uuid.uuid4())
    _get_client().collection(_COLLECTION).document(doc_id).set(
        {
            "source": source,
            "repo_or_doc_id": repo_or_doc_id,
            "trigger_ref": trigger_ref,
            "commit_message": commit_message,
            "changed_files": changed_files or [],
            "risk_level": (report.get("risk_assessment") or {}).get("risk_level"),
            "status": "pending",  # pending | resolved — UR: 검토 대기 워크플로
            "extracted_context": report.get("extracted_context"),
            "patent_search_results": report.get("patent_search_results"),
            "risk_assessment": report.get("risk_assessment"),
            "final_report": report.get("final_report"),
            "license_review": license_review or [],
            "created_at": datetime.datetime.now(datetime.timezone.utc),
        }
    )
    return doc_id


def list_reports(limit: int = 200) -> list[dict]:
    """리포트 이력을 최신순으로 가져온다. 위험도/상태 필터링은 호출부에서 수행한다."""
    query = (
        _get_client()
        .collection(_COLLECTION)
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    docs = query.stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]


def get_report(report_id: str) -> dict | None:
    """리포트 하나를 ID로 조회한다 (상세 페이지용)."""
    doc = _get_client().collection(_COLLECTION).document(report_id).get()
    if not doc.exists:
        return None
    return {"id": doc.id, **doc.to_dict()}


def mark_resolved(report_id: str) -> None:
    """리포트를 검토 완료 상태로 표시한다. '리포트 이력'(검토 대기) 목록에서 빠지고,
    '전체 이력'에는 '해결됨' 표시와 함께 계속 남는다."""
    _get_client().collection(_COLLECTION).document(report_id).update({"status": "resolved"})


def mark_pending(report_id: str) -> None:
    """해결됨 표시를 실수로 눌렀을 때 되돌리는 용도 — 다시 검토 대기 상태로 되돌린다."""
    _get_client().collection(_COLLECTION).document(report_id).update({"status": "pending"})


def get_dashboard_stats() -> dict:
    """대시보드 지표 카드용 집계. 소규모 프로젝트 스케일이라 전체를 읽어 파이썬에서 집계한다."""
    docs = _get_client().collection(_COLLECTION).stream()
    all_reports = [d.to_dict() for d in docs]

    now = datetime.datetime.now(datetime.timezone.utc)
    week_ago = now - datetime.timedelta(days=7)

    total = 0
    pending_count = 0
    risky_this_week = 0

    for r in all_reports:
        total += 1
        risk = r.get("risk_level") or "low"
        status = r.get("status") or "pending"
        if status == "pending" and risk in ("medium", "high"):
            pending_count += 1
        created = r.get("created_at")
        if created and risk in ("medium", "high") and created >= week_ago:
            risky_this_week += 1

    return {
        "total_reports": total,
        "risky_this_week": risky_this_week,
        "pending_count": pending_count,
    }


def add_connected_repo(
    repo: str, *, webhook_id: int | None = None, secret_name: str | None = None
) -> str:
    """저장소를 '연결됨' 상태로 진짜로 기록한다. 리포트 존재 여부로 추측하는 방식은
    Firestore를 지워도 GitHub 웹훅은 그대로 남아있어 부정확했다 — 이 컬렉션이 진실의 원천.

    secret_name: 이 저장소의 GitHub PAT이 저장된 Secret Manager 리소스 경로.
    (레거시 연결처럼 없으면 호출부가 GITHUB_ACCESS_TOKEN 환경변수로 폴백한다.)
    """
    doc_id = str(uuid.uuid4())
    doc: dict = {
        "repo": repo,
        "webhook_id": webhook_id,
        "connected_at": datetime.datetime.now(datetime.timezone.utc),
    }
    if secret_name:
        doc["secret_name"] = secret_name
    _get_client().collection(_CONNECTED_REPOS_COLLECTION).document(doc_id).set(doc)
    return doc_id


def update_connected_repo(repo: str, **fields) -> None:
    """이미 연결된 저장소의 필드(예: secret_name 재발급)를 갱신한다.
    새 문서를 만들지 않고 기존 문서를 그대로 덮어써서 중복 레코드를 막는다."""
    for r in list_connected_repos():
        if r.get("repo") == repo:
            _get_client().collection(_CONNECTED_REPOS_COLLECTION).document(r["id"]).update(fields)
            return


def list_connected_repos() -> list[dict]:
    docs = _get_client().collection(_CONNECTED_REPOS_COLLECTION).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]


def get_connected_repo(repo: str) -> dict | None:
    for r in list_connected_repos():
        if r.get("repo") == repo:
            return r
    return None


def remove_connected_repo(repo: str) -> None:
    """연결 해제 — 이 함수만으로는 GitHub 웹훅 자체는 안 지워진다.
    호출부(main.py)에서 delete_webhook과 함께 호출해야 진짜로 끊어진다."""
    for r in list_connected_repos():
        if r.get("repo") == repo:
            _get_client().collection(_CONNECTED_REPOS_COLLECTION).document(r["id"]).delete()
            return
