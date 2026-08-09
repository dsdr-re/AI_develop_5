"""IP Sentinel Cloud Run 엔트리포인트.

GitHub push 웹훅을 받으면:
1. 서명(HMAC) 검증
2. 변경된 커밋의 diff를 가져옴
3. 5-Agent 파이프라인 실행 (Context Extraction → Patent Search → Risk Assessment → Reporter)
4. 결과를 Firestore에 저장

Drive 연동(F-03)은 아직 미구현 — TODO 표시.
"""

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request

from agents.pipeline import run_pipeline
from services.firestore_store import save_report
from services.github_client import extract_push_event_info, get_commit_diff, verify_webhook_signature

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ip-sentinel")

app = FastAPI(title="IP Sentinel")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
):
    body = await request.body()

    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if secret and not verify_webhook_signature(body, x_hub_signature_256, secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if x_github_event != "push":
        # PR 이벤트 등은 F-01 MVP 범위에서는 처리하지 않음 (5.1 참고)
        return {"status": "ignored", "event": x_github_event}

    payload = await request.json()
    info = extract_push_event_info(payload)

    if not info["commit_sha"]:
        return {"status": "ignored", "reason": "no head_commit"}

    logger.info("push event received: %s/%s @ %s", info["owner"], info["repo"], info["commit_sha"])

    diff_text = await get_commit_diff(info["owner"], info["repo"], info["commit_sha"])
    if not diff_text.strip():
        return {"status": "skipped", "reason": "empty diff"}

    result = await run_pipeline(diff_text)

    doc_id = save_report(
        source="github",
        repo_or_doc_id=f"{info['owner']}/{info['repo']}",
        trigger_ref=info["commit_sha"],
        report=result,
    )

    logger.info("report saved: %s (risk=%s)", doc_id, (result.get("risk_assessment") or {}).get("risk_level"))

    return {
        "status": "ok",
        "report_id": doc_id,
        "risk_level": (result.get("risk_assessment") or {}).get("risk_level"),
        "final_report": result.get("final_report"),
    }


# TODO (F-03, Should): Google Drive 변경사항을 받는 /webhook/drive 엔드포인트.
#   Drive는 push 웹훅이 없으므로 Drive Activity API 폴링 또는 Pub/Sub 알림 채널 구독이 필요.


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
