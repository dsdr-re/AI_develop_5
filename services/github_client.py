"""GitHub 저장소 변경사항(커밋/PR diff) 추적 클라이언트.

웹훅(push, pull_request) payload를 받아 실제 diff 텍스트를 가져오는 역할.
Orchestrator 에이전트가 이 모듈로 diff를 확보한 뒤 파이프라인에 넘긴다.
"""

from __future__ import annotations

import hashlib
import hmac
import os

import httpx

GITHUB_API_BASE = "https://api.github.com"


def verify_webhook_signature(payload_body: bytes, signature_header: str | None, secret: str) -> bool:
    """GitHub webhook의 X-Hub-Signature-256 헤더를 검증한다."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), payload_body, hashlib.sha256).hexdigest()
    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)


async def get_commit_diff(
    owner: str,
    repo: str,
    commit_sha: str,
    *,
    token: str | None = None,
    timeout: float = 15.0,
) -> str:
    """단일 커밋의 diff(patch)를 텍스트로 가져온다."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{commit_sha}"
    headers = {"Accept": "application/vnd.github+json"}
    auth_token = token or os.environ.get("GITHUB_ACCESS_TOKEN")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    patches = []
    for f in data.get("files", []):
        filename = f.get("filename")
        patch = f.get("patch")
        if patch:
            patches.append(f"--- {filename} ---\n{patch}")
    return "\n\n".join(patches)


async def get_pull_request_diff(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    token: str | None = None,
    timeout: float = 15.0,
) -> str:
    """PR 전체 diff를 텍스트로 가져온다."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = {"Accept": "application/vnd.github.v3.diff"}
    auth_token = token or os.environ.get("GITHUB_ACCESS_TOKEN")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text


async def post_commit_comment(
    owner: str,
    repo: str,
    commit_sha: str,
    body: str,
    *,
    token: str | None = None,
    timeout: float = 15.0,
) -> dict:
    """커밋에 IP Sentinel 리포트를 댓글로 남긴다.

    Firestore/로그를 뒤지지 않아도, 커밋을 만든 바로 그 자리에서 리포트를
    확인할 수 있게 하는 게 목적 (UR-01: 별도 시간 없이 자동으로 확인).
    """
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{commit_sha}/comments"
    headers = {"Accept": "application/vnd.github+json"}
    auth_token = token or os.environ.get("GITHUB_ACCESS_TOKEN")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json={"body": body})
        resp.raise_for_status()
        return resp.json()


async def create_webhook(
    owner: str,
    repo: str,
    webhook_url: str,
    secret: str,
    *,
    token: str | None = None,
    timeout: float = 15.0,
) -> dict:
    """저장소에 push 웹훅을 자동으로 등록한다.

    호출자가 그 저장소의 admin 권한을 가진 토큰을 갖고 있어야 성공한다.
    이미 같은 URL로 웹훅이 등록돼 있으면 GitHub이 422("Hook already exists on this
    repository")를 반환한다 — 이 경우 실제로는 실패가 아니라 이미 정상 연결된 상태이므로,
    호출부에서 에러 메시지에 "already exists"가 포함돼 있는지 보고 구분해야 한다.
    """
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/hooks"
    headers = {"Accept": "application/vnd.github+json"}
    auth_token = token or os.environ.get("GITHUB_ACCESS_TOKEN")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    payload = {
        "name": "web",
        "active": True,
        "events": ["push"],
        "config": {"url": webhook_url, "content_type": "json", "secret": secret},
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            detail = resp.text[:200]
            try:
                body = resp.json()
                detail = body.get("message") or detail
                # GitHub 422 응답은 진짜 이유(예: "Hook already exists on this repository")를
                # 겉의 message가 아니라 errors[].message 안에 넣어서 준다. 이걸 놓치면
                # "이미 등록됨"을 감지하지 못해 정상 상황도 실패로 잘못 표시된다.
                sub_messages = [
                    e.get("message") for e in (body.get("errors") or []) if isinstance(e, dict) and e.get("message")
                ]
                if sub_messages:
                    detail = f"{detail}: {'; '.join(sub_messages)}"
            except Exception:
                pass
            raise RuntimeError(f"GitHub 웹훅 등록 실패 ({resp.status_code}): {detail}")
        return resp.json()


async def find_webhook_id(
    owner: str,
    repo: str,
    webhook_url: str,
    *,
    token: str | None = None,
    timeout: float = 15.0,
) -> int | None:
    """저장소에 등록된 웹훅 중 우리 URL과 일치하는 것의 ID를 찾는다.

    create_webhook이 "이미 존재함"으로 실패했을 때, 그 기존 웹훅을 우리 추적
    목록에 편입(adopt)시키기 위한 용도 — 그래야 나중에 연결 해제도 가능해진다.
    """
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/hooks"
    headers = {"Accept": "application/vnd.github+json"}
    auth_token = token or os.environ.get("GITHUB_ACCESS_TOKEN")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        hooks = resp.json()
    for h in hooks:
        if isinstance(h, dict) and h.get("config", {}).get("url") == webhook_url:
            return h.get("id")
    return None


async def delete_webhook(
    owner: str,
    repo: str,
    hook_id: int,
    *,
    token: str | None = None,
    timeout: float = 15.0,
) -> None:
    """저장소에서 웹훅을 실제로 삭제한다 (연결 해제)."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/hooks/{hook_id}"
    headers = {"Accept": "application/vnd.github+json"}
    auth_token = token or os.environ.get("GITHUB_ACCESS_TOKEN")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.delete(url, headers=headers)
        # 404는 "이미 없음" — 결과적으로 목표(웹훅 없음)는 달성된 것이므로 에러 취급 안 함
        if resp.status_code not in (204, 404):
            raise RuntimeError(f"GitHub 웹훅 삭제 실패 ({resp.status_code}): {resp.text[:200]}")


async def get_default_branch(
    owner: str,
    repo: str,
    *,
    token: str | None = None,
    timeout: float = 15.0,
) -> str:
    """저장소의 기본 브랜치 이름을 가져온다 (main/master 등 무엇이든 대응하기 위함)."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
    headers = {"Accept": "application/vnd.github+json"}
    auth_token = token or os.environ.get("GITHUB_ACCESS_TOKEN")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json().get("default_branch") or "main"


_RELEVANT_EXTENSIONS = (".md", ".py")


def _is_relevant_file(path: str) -> bool:
    """초기 스캔 대상 파일인지 판단한다 (기획 문서 .md, 코드 .py, requirements.txt).
    knowledge/ 폴더는 RAG용 지식 문서(라이선스 가이드 등)라 실제 기획/코드가
    아니므로 제외한다.
    """
    if path.startswith("knowledge/"):
        return False
    filename = path.rsplit("/", 1)[-1]
    return path.endswith(_RELEVANT_EXTENSIONS) or filename == "requirements.txt"


async def list_repo_files(
    owner: str,
    repo: str,
    *,
    branch: str,
    token: str | None = None,
    timeout: float = 20.0,
) -> list[str]:
    """저장소 안의 관련 파일(.md, .py, requirements.txt) 경로 목록을 가져온다.

    초기 연결 시 "이미 있던 내용"을 스캔하기 위한 용도.
    """
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees/{branch}"
    headers = {"Accept": "application/vnd.github+json"}
    auth_token = token or os.environ.get("GITHUB_ACCESS_TOKEN")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers, params={"recursive": "1"})
        resp.raise_for_status()
        data = resp.json()

    paths = []
    for item in data.get("tree", []):
        if item.get("type") == "blob":
            path = item.get("path", "")
            if _is_relevant_file(path):
                paths.append(path)
    return paths


async def get_file_content(
    owner: str,
    repo: str,
    path: str,
    *,
    token: str | None = None,
    timeout: float = 15.0,
) -> str:
    """저장소 안의 특정 파일 전체 내용을 텍스트로 가져온다."""
    import base64

    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}"
    headers = {"Accept": "application/vnd.github+json"}
    auth_token = token or os.environ.get("GITHUB_ACCESS_TOKEN")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    content_b64 = data.get("content", "")
    if not content_b64:
        return ""
    try:
        return base64.b64decode(content_b64).decode("utf-8", errors="replace")
    except Exception:
        return ""


def extract_push_event_info(payload: dict) -> dict:
    """push 웹훅 payload에서 오케스트레이터가 필요로 하는 최소 정보를 추출한다."""
    repo_full = payload.get("repository", {}).get("full_name", "")
    owner, _, repo = repo_full.partition("/")
    head_commit = payload.get("head_commit") or {}
    return {
        "owner": owner,
        "repo": repo,
        "commit_sha": head_commit.get("id"),
        "commit_message": head_commit.get("message"),
        "pusher": payload.get("pusher", {}).get("name"),
    }
