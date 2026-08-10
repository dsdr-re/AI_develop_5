"""IP Sentinel Cloud Run 엔트리포인트."""

import logging
import os

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from agents.pipeline import run_pipeline
from services.firestore_store import get_report, list_reports, save_report
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

_PAGE_STYLE = """
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          max-width: 960px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; }
  h1 { font-size: 22px; margin-bottom: 4px; }
  h2 { font-size: 16px; margin-top: 28px; margin-bottom: 8px; }
  .sub { color: #666; font-size: 14px; margin-top: 0; }
  table { width: 100%; border-collapse: collapse; margin-top: 20px; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid #e5e5e5; font-size: 14px; }
  th { color: #666; font-weight: 600; font-size: 13px; }
  .badge { color: white; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; white-space: nowrap; }
  .summary { color: #333; max-width: 320px; }
  a { color: #2563eb; text-decoration: none; }
  .empty { color: #888; padding: 40px 0; text-align: center; }
  .empty-inline { color: #888; font-size: 14px; }
  .back { display: inline-block; margin-bottom: 16px; font-size: 14px; }
  .box { background: #f7f7f8; border-radius: 10px; padding: 16px 18px; margin-top: 8px; font-size: 14px; line-height: 1.6; }
  ul.patents { list-style: none; padding: 0; margin: 8px 0 0; }
  ul.patents li { padding: 12px 0; border-bottom: 1px solid #e5e5e5; }
  ul.patents .meta { color: #888; font-size: 13px; font-weight: normal; }
  ul.patents .note { color: #555; font-size: 13px; margin-top: 4px; }
"""


@app.get("/reports", response_class=HTMLResponse)
async def reports_page():
    reports = list_reports(limit=50)

    rows = []
    for r in reports:
        risk = r.get("risk_level") or "unknown"
        color, label = _RISK_STYLE.get(risk, ("#888", risk or "확인필요"))
        repo = r.get("repo_or_doc_id", "")
        ref = (r.get("trigger_ref") or "")[:7]
        created = r.get("created_at")
        created_str = created.strftime("%Y-%m-%d %H:%M") if created else ""
        summary = ""
        extracted = r.get("extracted_context")
        if isinstance(extracted, dict):
            summary = extracted.get("summary") or ""
        detail_url = f"/reports/{r.get('id', '')}"
        rows.append(
            f"<tr><td><span class='badge' style='background:{color}'>{label}</span></td>"
            f"<td>{repo}</td>"
            f"<td><a href='{detail_url}'>{ref}</a></td>"
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
<style>{_PAGE_STYLE}</style>
</head>
<body>
  <h1>IP Sentinel 리포트 이력</h1>
  <p class="sub">최근 {len(reports)}건. 커밋 번호를 클릭하면 상세 리포트를 볼 수 있습니다.</p>
  <table>
    <tr><th>위험도</th><th>저장소</th><th>커밋</th><th>요약</th><th>시각</th></tr>
    {rows_html}
  </table>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/reports/{report_id}", response_class=HTMLResponse)
async def report_detail_page(report_id: str):
    r = get_report(report_id)
    if not r:
        return HTMLResponse(content="<p>리포트를 찾을 수 없습니다.</p>", status_code=404)

    risk = r.get("risk_level") or "unknown"
    color, label = _RISK_STYLE.get(risk, ("#888", risk or "확인필요"))
    repo = r.get("repo_or_doc_id", "")
    full_ref = r.get("trigger_ref") or ""
    github_url = f"https://github.com/{repo}/commit/{full_ref}" if repo and full_ref else "#"

    extracted = r.get("extracted_context")
    summary = extracted.get("summary") if isinstance(extracted, dict) else ""

    risk_assessment = r.get("risk_assessment")
    rationale = risk_assessment.get("rationale") if isinstance(risk_assessment, dict) else ""
    recommended_action = risk_assessment.get("recommended_action") if isinstance(risk_assessment, dict) else ""

    patent_results = r.get("patent_search_results")
    matches_html = "<p class='empty-inline'>참고한 특허가 없습니다.</p>"
    if isinstance(patent_results, dict):
        matches = patent_results.get("matches") or []
        if matches:
            items = []
            for m in matches:
                title = m.get("title") or "(제목 없음)"
                app_no = m.get("application_number") or "번호 미상"
                status = m.get("registration_status") or "상태 미상"
                note = m.get("relevance_note") or ""
                items.append(
                    f"<li><strong>{title}</strong><br>"
                    f"<span class='meta'>출원번호 {app_no} · 등록상태 {status}</span>"
                    f"<div class='note'>{note}</div></li>"
                )
            matches_html = "<ul class='patents'>" + "".join(items) + "</ul>"

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>IP Sentinel 리포트 상세</title>
<style>{_PAGE_STYLE}</style>
</head>
<body>
  <a class="back" href="/reports">&larr; 목록으로</a>
  <h1><span class='badge' style='background:{color}'>{label}</span> &nbsp;{repo}</h1>
  <p class="sub"><a href="{github_url}" target="_blank" rel="noopener">GitHub 커밋에서 보기 →</a></p>

  <h2>요약</h2>
  <div class="box">{summary or '요약 없음'}</div>

  <h2>근거</h2>
  <div class="box">{rationale or '근거 없음'}</div>

  <h2>권장 액션</h2>
  <div class="box">{recommended_action or '권장 액션 없음'}</div>

  <h2>참고한 특허</h2>
  {matches_html}
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

    return {"status": "accepted", "message": "분석을 시작했습니다. /reports 페이지나 커밋 댓글에서 확인하세요."}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
