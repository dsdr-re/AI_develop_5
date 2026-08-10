"""IP Sentinel Cloud Run 엔트리포인트."""

import datetime
import logging
import os
import re

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from agents.pipeline import run_pipeline
from services.firestore_store import get_dashboard_stats, get_report, list_reports, save_report
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
    "low": ("var(--bg-success)", "var(--text-success)", "낮음"),
    "medium": ("var(--bg-warning)", "var(--text-warning)", "중간"),
    "high": ("var(--bg-danger)", "var(--text-danger)", "높음"),
}

_CODE_EXTENSIONS = (".py", ".js", ".ts", ".java", ".go", ".rb", ".c", ".cpp", ".jsx", ".tsx")

_ICON_CODE = (
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>'
)
_ICON_DOC = (
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>'
    '<polyline points="14 2 14 8 20 8"></polyline></svg>'
)

_PAGE_STYLE = """
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          max-width: 960px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; }
  h1 { font-size: 20px; margin-bottom: 4px; font-weight: 500; }
  h2 { font-size: 16px; margin-top: 28px; margin-bottom: 8px; font-weight: 500; }
  .sub { color: #666; font-size: 14px; margin-top: 0; }
  table { width: 100%; border-collapse: collapse; margin-top: 20px; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 0.5px solid #e5e5e5; font-size: 14px; }
  th { color: #666; font-weight: 600; font-size: 13px; }
  .badge { padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; white-space: nowrap; }
  .summary { color: #333; max-width: 320px; }
  a { color: #2563eb; text-decoration: none; }
  .empty { color: #888; padding: 40px 0; text-align: center; }
  .empty-inline { color: #888; font-size: 14px; }
  .back { display: inline-block; margin-bottom: 16px; font-size: 14px; color: #2563eb; cursor: pointer; }
  .box { background: #f7f7f8; border-radius: 10px; padding: 16px 18px; margin-top: 8px; font-size: 14px; line-height: 1.6; }
  ul.patents { list-style: none; padding: 0; margin: 8px 0 0; }
  ul.patents li { padding: 12px 0; border-bottom: 0.5px solid #e5e5e5; }
  ul.patents .meta { color: #888; font-size: 13px; font-weight: normal; }
  ul.patents .note { color: #555; font-size: 13px; margin-top: 4px; }
  .navbar { display: flex; align-items: center; justify-content: space-between;
            padding-bottom: 16px; border-bottom: 0.5px solid #e5e5e5; margin-bottom: 24px; }
  .navbar-left { display: flex; align-items: center; gap: 10px; }
  .navbar-icon { width: 32px; height: 32px; border-radius: 8px; background: #e6f1fb;
                 display: flex; align-items: center; justify-content: center; color: #185fa5; }
  .navbar-title { font-size: 16px; font-weight: 500; }
  .navbar-links { display: flex; gap: 20px; }
  .navbar-links a { font-size: 14px; color: #666; }
  .navbar-links a.active { color: #1a1a1a; font-weight: 500; }
  .metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
                 gap: 12px; margin-bottom: 28px; }
  .metric-card { background: #f7f7f8; border-radius: 8px; padding: 1rem; }
  .metric-label { font-size: 13px; color: #666; margin: 0 0 6px; }
  .metric-value { font-size: 24px; font-weight: 500; margin: 0; }
  .feed-card { display: flex; align-items: center; gap: 14px; border: 0.5px solid #e5e5e5;
               border-radius: 12px; padding: 14px 16px; margin-bottom: 10px; }
  .feed-icon { color: #888; flex-shrink: 0; }
  .feed-title { font-size: 14px; font-weight: 500; margin: 0; }
  .feed-sub { font-size: 12px; color: #888; margin: 3px 0 0; }
"""


def _nav_html(active: str) -> str:
    dash_cls = "active" if active == "dashboard" else ""
    list_cls = "active" if active == "list" else ""
    return f"""
<div class="navbar">
  <div class="navbar-left">
    <div class="navbar-icon">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
      </svg>
    </div>
    <span class="navbar-title">IP-Sentinel</span>
  </div>
  <div class="navbar-links">
    <a href="/" class="{dash_cls}">대시보드</a>
    <a href="/reports" class="{list_cls}">리포트 이력</a>
  </div>
</div>
"""


def _icon_for(filename: str) -> str:
    return _ICON_CODE if any(filename.endswith(ext) for ext in _CODE_EXTENSIONS) else _ICON_DOC


def _relative_time(dt: datetime.datetime | None) -> str:
    if not dt:
        return ""
    now = datetime.datetime.now(datetime.timezone.utc)
    diff = (now - dt).total_seconds()
    if diff < 3600:
        return f"{max(int(diff // 60), 1)}분 전"
    if diff < 86400:
        return f"{int(diff // 3600)}시간 전"
    days = int(diff // 86400)
    if days == 1:
        return "어제"
    if days < 7:
        return f"{days}일 전"
    return dt.strftime("%Y-%m-%d")


def _file_label(r: dict) -> str:
    files = r.get("changed_files") or []
    if not files:
        return "(변경 파일 정보 없음)"
    if len(files) == 1:
        return files[0]
    return f"{files[0]} 외 {len(files) - 1}개"


@app.get("/", response_class=HTMLResponse)
async def dashboard_page():
    stats = get_dashboard_stats()
    recent = list_reports(limit=3)

    cards = []
    for r in recent:
        risk = r.get("risk_level") or "low"
        bg, text, label = _RISK_STYLE.get(risk, ("#eee", "#666", risk))
        file_label = _file_label(r)
        primary_file = (r.get("changed_files") or [""])[0]
        msg = r.get("commit_message") or "(커밋 메시지 없음)"
        rel_time = _relative_time(r.get("created_at"))
        cards.append(
            f"<div class='feed-card' onclick=\"location.href='/reports/{r.get('id','')}'\" style='cursor:pointer;'>"
            f"<span class='feed-icon'>{_icon_for(primary_file)}</span>"
            f"<div style='flex:1;'>"
            f"<p class='feed-title'>{file_label} — {msg}</p>"
            f"<p class='feed-sub'>GitHub · main 브랜치 · {rel_time}</p>"
            f"</div>"
            f"<span class='badge' style='background:{bg}; color:{text};'>{label}</span></div>"
        )
    cards_html = "".join(cards) if cards else "<p class='empty-inline'>아직 탐지된 변경사항이 없습니다.</p>"

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>IP Sentinel 대시보드</title>
<style>{_PAGE_STYLE}</style>
</head>
<body>
  {_nav_html('dashboard')}
  <div class="metric-grid">
    <div class="metric-card"><p class="metric-label">연결된 워크스페이스</p><p class="metric-value">{stats['workspace_count']}</p></div>
    <div class="metric-card"><p class="metric-label">전체 리포트</p><p class="metric-value">{stats['total_reports']}</p></div>
    <div class="metric-card"><p class="metric-label">이번 주 위험 발견</p><p class="metric-value" style="color:#c98a1f;">{stats['risky_this_week']}</p></div>
    <div class="metric-card"><p class="metric-label">낮음 (안전)</p><p class="metric-value" style="color:#1a9c5b;">{stats['low_count']}</p></div>
  </div>
  <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px;">
    <p style="font-size:16px; font-weight:500; margin:0;">최근 탐지된 변경사항</p>
    <a href="/reports" style="font-size:13px;">전체 이력 보기 →</a>
  </div>
  {cards_html}
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/reports", response_class=HTMLResponse)
async def reports_page():
    reports = list_reports(limit=50)

    rows = []
    for r in reports:
        risk = r.get("risk_level") or "unknown"
        bg, text, label = _RISK_STYLE.get(risk, ("#eee", "#666", risk or "확인필요"))
        repo = r.get("repo_or_doc_id", "")
        created = r.get("created_at")
        created_str = created.strftime("%Y-%m-%d %H:%M") if created else ""
        summary = ""
        extracted = r.get("extracted_context")
        if isinstance(extracted, dict):
            summary = extracted.get("summary") or ""
        msg = r.get("commit_message") or "(커밋 메시지 없음)"
        file_label = _file_label(r)
        detail_url = f"/reports/{r.get('id', '')}"
        rows.append(
            f"<tr><td><span class='badge' style='background:{bg}; color:{text};'>{label}</span></td>"
            f"<td>{repo}</td>"
            f"<td><a href='{detail_url}'>{msg}</a><div style='font-size:12px; color:#888; margin-top:2px;'>{file_label}</div></td>"
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
  {_nav_html('list')}
  <h1>IP Sentinel 리포트 이력</h1>
  <p class="sub">최근 {len(reports)}건. 변경사항을 클릭하면 상세 리포트를 볼 수 있습니다.</p>
  <table>
    <tr><th>위험도</th><th>저장소</th><th>변경사항</th><th>요약</th><th>시각</th></tr>
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
    bg, text, label = _RISK_STYLE.get(risk, ("#eee", "#666", risk or "확인필요"))
    repo = r.get("repo_or_doc_id", "")
    full_ref = r.get("trigger_ref") or ""
    github_url = f"https://github.com/{repo}/commit/{full_ref}" if repo and full_ref else "#"
    msg = r.get("commit_message") or "(커밋 메시지 없음)"
    file_label = _file_label(r)
    created = r.get("created_at")
    created_str = created.strftime("%Y-%m-%d %H:%M") if created else ""

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
  {_nav_html('list')}
  <span class="back" onclick="history.back()">&larr; 뒤로</span>
  <div style="display:flex; align-items:center; gap:10px; margin-bottom:4px;">
    <span class='badge' style='background:{bg}; color:{text};'>{label}</span>
    <span style="font-size:16px; font-weight:500;">{msg}</span>
  </div>
  <p class="sub">{repo} · {created_str} · 변경 파일: {file_label}
    · <a href="{github_url}" target="_blank" rel="noopener">GitHub에서 보기 →</a></p>

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


def _extract_changed_files(diff_text: str) -> list[str]:
    return re.findall(r"^--- (.+?) ---$", diff_text, re.MULTILINE)


async def _process_push_event(info: dict, diff_text: str) -> None:
    if not diff_text.strip():
        logger.info("empty diff, skip: %s/%s @ %s", info["owner"], info["repo"], info["commit_sha"])
        return

    result = await run_pipeline(diff_text)
    changed_files = _extract_changed_files(diff_text)

    doc_id = save_report(
        source="github",
        repo_or_doc_id=f"{info['owner']}/{info['repo']}",
        trigger_ref=info["commit_sha"],
        report=result,
        commit_message=info.get("commit_message") or "",
        changed_files=changed_files,
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

    return {"status": "accepted", "message": "분석을 시작했습니다. 대시보드나 리포트 이력에서 확인하세요."}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
