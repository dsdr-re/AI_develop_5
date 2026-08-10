"""IP Sentinel Cloud Run 엔트리포인트."""

import logging
import os

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from agents.pipeline import run_pipeline
from services.firestore_store import list_reports, save_report
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


_RISK_STYLE = {
    "low": ("#1a9c5b", "낮음"),
    "medium": ("#c98a1f", "중간"),
    "high": ("#d9364f", "높음"),
}


@app.get("/reports", response_class=HTMLResponse)
async def reports_page():
    reports = list_reports(limit=50)

    rows = []
    for r in reports:
        risk = r.get("risk_level") or "unknown"
        color, label = _RISK_STYLE.get(risk, ("#888", risk or "확인필요"))
        repo = r.get("repo_or_doc_id", "")
        ref = (r.get("trigger_ref") or "")[:7]
        full_ref = r.get("trigger_ref") or ""
        created = r.get("created_at")
        created_str = created.strftime("%Y-%m-%d %H:%M") if created else ""
        github_url = f"https://github.com/{repo}/commit/{full_ref}" if repo and full_ref else "#"
        summary = ""
        extracted = r.get("extracted_context")
        if isinstance(extracted, dict):
            summary = extracted.get("summary") or ""
        rows.append(
            f"<tr><td><span class='badge' style='background:{color}'>{label}</span></td>"
            f"<td>{repo}</td>"
            f"<td><a href='{github_url}' target='_blank' rel='noopener'>{ref}</a></td>"
            f"<td class='summary'>{summary}</td>"
            f"<td>{created_str}</td></tr>"
        )

    rows_html = "".join(rows) if rows else (
        "<tr><td colspan='5' class='empty'>아직 리포트가 없습니다. "
        "커밋을 하나 올려서 웹훅이 동작하는지 확인해보세요.</td></tr>"
    )

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>IP Sentinel 리포트 이력</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          max-width: 960px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .sub {{ color: #666; font-size: 14px; margin-top: 0; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #e5e5e5; font-size: 14px; }}
  th {{ color: #666; font-weight: 600; font-size: 13px; }}
  .badge {{ color: white; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; white-space: nowrap; }}
  .summary {{ color: #333; max-width: 360px; }}
  a {{ color: #2563eb; text-decoration: none; }}
  .empty {{ color: #888; padding: 40px 0; text-align: center; }}
</style>
</head>
<body>
  <h1>IP Sentinel 리포트 이력</h1>
  <p class="sub">최근 {len(reports)}건. 커밋 번호를 클릭하면 GitHub에서 전체 리포트를 볼 수 있습니다.</p>
  <table>
    <tr><th>위험도</th><th>저장소</th><th>커밋</th><th>요약</th><th>시각</th></tr>
    {rows_html}
  </table>
</body>
</html>"""
    return HTMLResponse(content=html)


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

    return {"status": "accepted", "message": "분석을 시작했습니다. 커밋 댓글이나 /reports 페이지에서 확인하세요."}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
