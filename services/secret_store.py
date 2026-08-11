"""워크스페이스(연결된 저장소)별 GitHub PAT을 Secret Manager에 저장/조회한다.

지금까지는 GITHUB_ACCESS_TOKEN 환경변수 하나(무성 개인 PAT)에 모든 저장소가
의존해서, 실제로는 그 계정이 admin 권한을 가진 저장소만 연결할 수 있었다.
이 모듈은 저장소(repo)마다 별도 Secret Manager 시크릿을 동적으로 만들어
사용자가 직접 입력한 토큰을 저장하고, 이후 요청에서 그 저장소의 토큰을
다시 꺼내 쓸 수 있게 한다. 토큰 원문은 Firestore가 아니라 여기(Secret
Manager)에만 저장되고, Firestore에는 이 시크릿의 리소스 경로만 남는다.
"""

from __future__ import annotations

import re

import google.auth
from google.api_core import exceptions as gcp_exceptions
from google.cloud import secretmanager

_client: secretmanager.SecretManagerServiceClient | None = None
_project_id: str | None = None


def _get_client() -> secretmanager.SecretManagerServiceClient:
    global _client
    if _client is None:
        _client = secretmanager.SecretManagerServiceClient()
    return _client


def _get_project_id() -> str:
    global _project_id
    if _project_id is None:
        _, project_id = google.auth.default()
        if not project_id:
            raise RuntimeError("GCP 프로젝트를 확인할 수 없습니다 (google.auth.default() 실패)")
        _project_id = project_id
    return _project_id


def _secret_id_for(repo: str) -> str:
    """'owner/repo' -> Secret Manager가 허용하는 ID로 변환.

    예: 'dsdr-re/AI_develop_5' -> 'workspace-dsdr-re-ai-develop-5-github-token'
    """
    safe = re.sub(r"[^a-zA-Z0-9-]", "-", repo.strip())
    safe = re.sub(r"-{2,}", "-", safe).strip("-").lower()
    return f"workspace-{safe}-github-token"


def save_workspace_token(repo: str, token: str) -> str:
    """repo에 대한 PAT을 Secret Manager에 저장하고, Firestore에 남길
    리소스 경로(secret_name)를 반환한다. 시크릿이 이미 있으면 새 버전만 추가한다
    (재연결 시 토큰 갱신을 지원하기 위함).
    """
    client = _get_client()
    project_id = _get_project_id()
    secret_id = _secret_id_for(repo)
    parent = f"projects/{project_id}"
    secret_path = f"{parent}/secrets/{secret_id}"

    try:
        client.get_secret(name=secret_path)
    except gcp_exceptions.NotFound:
        client.create_secret(
            request={
                "parent": parent,
                "secret_id": secret_id,
                "secret": {"replication": {"automatic": {}}},
            }
        )

    # stdin으로 등록하면 트레일링 뉴라인이 섞여 "Illegal header value" 에러가 났던
    # 전례가 있어, 여기서도 개행 없이 원문 그대로 바이트로 저장한다.
    client.add_secret_version(
        request={"parent": secret_path, "payload": {"data": token.strip().encode("utf-8")}}
    )
    return secret_path


def get_workspace_token(secret_name: str | None) -> str | None:
    """secret_name(Secret Manager 리소스 경로)의 최신 버전 값을 가져온다.
    없거나 조회 실패 시 None을 반환한다 (호출부는 이 경우 환경변수 등으로 폴백 가능)."""
    if not secret_name:
        return None
    client = _get_client()
    try:
        response = client.access_secret_version(name=f"{secret_name}/versions/latest")
    except gcp_exceptions.NotFound:
        return None
    return response.payload.data.decode("utf-8").strip()
