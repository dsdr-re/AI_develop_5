"""IP Sentinel Cloud Run 엔트리포인트."""

import logging
import os

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from agents.pipeline import run_pipeline
from services.firestore_store import save_report
from services.github_client import (
    extract_push_event_info,
    get_commit_diff,
    post_commit_comment,
    verify_webhook_signature,
)

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ip-sentinel")

app = FastAPI(title="IP Sentinel")


@app.get("/health")
async def health():
    return {"status": "ok"}


async def _process_push_event(info: dict, diff_text: str) -> None:
    if not diff_text.strip():
        logger.info("empty diff, skip: %s/%s @ %s", info["owner"], info["repo"], info["commit_sha"])
        return

    result = await run_pipeline(diff_text)

    doc_id = save_report(
        source="github",
        repo_or_doc_id=f"{info['owner']}/{info['repo']}",
        trigger_ref=info["commit_sha"],
        report=result,
    )

    logger.info("report saved: %s (risk=%s)", doc_id, (result.get("risk_assessment") or {}).get("risk_level"))

    final_report = result.get("final_report")
    if final_report:
        try:
            await post_commit_comment(info["owner"], info["repo"], info["commit_sha"], final_report)
            logger.info("commit comment posted: %s/%s @ %s", info["owner"], info["repo"], info["commit_sha"])
        except Exception:
            logger.exception("failed to post commit comment")


@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
):
    body = await request.body()

    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if secret and not verify_webhook_signature(body, x_hub_signature_256, secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if x_github_event != "push":
        return {"status": "ignored", "event": x_github_event}

    payload = await request.json()
    info = extract_push_event_info(payload)

    if not info["commit_sha"]:
        return {"status": "ignored", "reason": "no head_commit"}

    logger.info("push event received: %s/%s @ %s", info["owner"], info["repo"], info["commit_sha"])

    diff_text = await get_commit_diff(info["owner"], info["repo"], info["commit_sha"])

    background_tasks.add_task(_process_push_event, info, diff_text)

    return {"status": "accepted", "message": "분석을 시작했습니다. 잠시 후 커밋에 댓글로 리포트가 달립니다."}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
