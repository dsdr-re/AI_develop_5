"""GitHub 저장소 변경사항(커밋/PR diff) 추적 클라이언트."""

from __future__ import annotations

import hashlib
import hmac
import os

import httpx

GITHUB_API_BASE = "https://api.github.com"


def verify_webhook_signature(payload_body: bytes, signature_header: str | None, secret: str) -> bool:
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
    """커밋에 IP Sentinel 리포트를 댓글로 남긴다."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{commit_sha}/comments"
    headers = {"Accept": "application/vnd.github+json"}
    auth_token = token or os.environ.get("GITHUB_ACCESS_TOKEN")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json={"body": body})
        resp.raise_for_status()
        return resp.json()


def extract_push_event_info(payload: dict) -> dict:
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
