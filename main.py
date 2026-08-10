"""IP Sentinel Cloud Run 엔트리포인트."""

import datetime
import logging
import os
import re
from collections import OrderedDict

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from agents.pipeline import run_pipeline
from services.firestore_store import get_dashboard_stats, get_report, list_reports, mark_resolved, save_report
from services.github_client import (
    extract_push_event_info,
    get_commit_diff,
    post_commit_comment,
    verify_webhook_signature,
)
from services.license_client import get_pypi_license

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ip-sentinel")

app = FastAPI(title="IP Sentinel")


@app.get("/health")
async def health():
    return {"status": "ok"}


_RISK_STYLE = {
    "low": ("#e3f5e9", "#1a7f37", "낮음"),
    "medium": ("#fdf1de", "#b25e09", "중간"),
    "high": ("#fbe6e6", "#c92a2a", "높음"),
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
  a { color: #2563eb; text-decoration: none; }
  .empty { color: #888; padding: 40px 0; text-align: center; }
  .empty-inline { color: #888; font-size: 14px; }
  .back { display: inline-block; margin-bottom: 16px; font-size: 14px; color: #2563eb; cursor: pointer; }
  .box { background: #f7f7f8; border-radius: 10px; padding: 16px 18px; margin-top: 8px; font-size: 14px; line-height: 1.6; }
  .badge { padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; white-space: nowrap; }
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
  .date-heading { font-size: 13px; font-weight: 500; color: #888; margin: 20px 0 8px; }
  .group-box { border: 0.5px solid #e5e5e5; border-radius: 12px; overflow: hidden; margin-bottom: 4px; }
  .row-link { text-decoration: none; color: inherit; display: flex; align-items: center;
              gap: 12px; padding: 12px 16px; border-bottom: 0.5px solid #e5e5e5; }
  .row-link:last-child { border-bottom: none; }
  .row-title { font-size: 14px; font-weight: 500; margin: 0; color: #1a1a1a; }
  .row-sub { font-size: 12px; color: #888; margin: 2px 0 0; }
  .row-time { font-size: 13px; color: #666; white-space: nowrap; }
  .status-tag { font-size: 12px; color: #888; margin-right: 4px; white-space: nowrap; }
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


def _date_bucket(dt: datetime.datetime | None) -> str:
    if not dt:
        return "날짜 미상"
    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.date()
    d = dt.date()
    if d == today:
        return "오늘"
    if d == today - datetime.timedelta(days=1):
        return "어제"
    if (today - d).days < 7:
        return "이번 주"
    return dt.strftime("%Y-%m-%d")


def _group_by_date(reports: list[dict]) -> "OrderedDict[str, list[dict]]":
    groups: "OrderedDict[str, list[dict]]" = OrderedDict()
    for r in reports:
        bucket = _date_bucket(r.get("created_at"))
        groups.setdefault(bucket, []).append(r)
    return groups


def _file_label(r: dict) -> str:
    files = r.get("changed_files") or []
    if not files:
        return "(변경 파일 정보 없음)"
    if len(files) == 1:
        return files[0]
    return f"{files[0]} 외 {len(files) - 1}개"


def _render_grouped_list(reports: list[dict], *, show_status: bool) -> str:
    if not reports:
        return ""
    groups = _group_by_date(reports)
    out = []
    for bucket, items in groups.items():
        rows = []
        for r in items:
            risk = r.get("risk_level") or "unknown"
            bg, text, label = _RISK_STYLE.get(risk, ("#eee", "#666", risk or "확인필요"))
            msg = r.get("commit_message") or "(커밋 메시지 없음)"
            file_label = _file_label(r)
            created = r.get("created_at")
            time_str = created.strftime("%H:%M") if created else ""
            status = r.get("status") or "pending"
            status_html = '<span class="status-tag">해결됨</span>' if (show_status and status == "resolved") else ""
            detail_url = f"/reports/{r.get('id', '')}"
            rows.append(
                f"<a class='row-link' href='{detail_url}'>"
                f"<span class='badge' style='background:{bg}; color:{text};'>{label}</span>"
                f"<div style='flex:1;'><p class='row-title'>{msg}</p>"
                f"<p class='row-sub'>{file_label}</p></div>"
                f"{status_html}"
                f"<span class='row-time'>{time_str}</span></a>"
            )
        out.append(f"<p class='date-heading'>{bucket}</p>")
        out.append(f"<div class='group-box'>{''.join(rows)}</div>")
    return "".join(out)


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
    <div class="metric-card"><p class="metric-label">이번 주 위험 발견</p><p class="metric-value" style="color:#c92a2a;">{stats['risky_this_week']}</p></div>
    <div class="metric-card"><p class="metric-label">검토 대기</p><p class="metric-value">{stats['pending_count']}</p></div>
  </div>
  <p style="font-size:16px; font-weight:500; margin:0 0 12px;">최근 탐지된 변경사항</p>
  {cards_html}
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/reports", response_class=HTMLResponse)
async def reports_page(view: str = "important"):
    all_reports = list_reports(limit=200)
    show_all = view == "all"

    if show_all:
        reports = all_reports
        title = "IP Sentinel 전체 리포트 이력"
        sub = f"전체 {len(reports)}건 표시 중"
        toggle_link = '<a href="/reports">&larr; 검토 대기만 보기</a>'
        empty_msg = "아직 리포트가 없습니다. 커밋을 하나 올려서 웹훅이 동작하는지 확인해보세요."
    else:
        reports = [
            r
            for r in all_reports
            if (r.get("status") or "pending") == "pending" and (r.get("risk_level") in ("medium", "high"))
        ]
        title = "IP Sentinel 리포트 이력"
        sub = "검토 대기 중인 중간·높음 항목만 표시합니다."
        toggle_link = '<a href="/reports?view=all">전체 이력 보기 →</a>'
        empty_msg = "검토 대기 중인 항목이 없습니다. 모두 처리했습니다."

    list_html = _render_grouped_list(reports, show_status=show_all)
    if not list_html:
        list_html = f"<p class='empty' >{empty_msg}</p>"

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{_PAGE_STYLE}</style>
</head>
<body>
  {_nav_html('list')}
  <div style="display:flex; align-items:flex-start; justify-content:space-between;">
    <div>
      <h1>{title}</h1>
      <p class="sub">{sub}</p>
    </div>
    <div style="font-size:13px; padding-top:4px;">{toggle_link}</div>
  </div>
  {list_html}
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
    status = r.get("status") or "pending"

    extracted = r.get("extracted_context")
    summary = extracted.get("summary") if isinstance(extracted, dict) else ""

    risk_assessment = r.get("risk_assessment")
    rationale = risk_assessment.get("rationale") if isinstance(risk_assessment, dict) else ""
    recommended_action = risk_assessment.get("recommended_action") if isinstance(risk_assessment, dict) else ""
    related_patent_numbers = (
        set(risk_assessment.get("related_patents") or []) if isinstance(risk_assessment, dict) else set()
    )

    patent_results = r.get("patent_search_results")
    all_matches = (patent_results.get("matches") or []) if isinstance(patent_results, dict) else []

    def _render_patent_item(m: dict) -> str:
        title = m.get("title") or "(제목 없음)"
        app_no = m.get("application_number") or "번호 미상"
        p_status = m.get("registration_status") or "상태 미상"
        note = m.get("relevance_note") or ""
        return (
            f"<li><strong>{title}</strong><br>"
            f"<span class='meta'>출원번호 {app_no} · 등록상태 {p_status}</span>"
            f"<div class='note'>{note}</div></li>"
        )

    relevant_matches = [m for m in all_matches if m.get("application_number") in related_patent_numbers]
    other_matches = [m for m in all_matches if m.get("application_number") not in related_patent_numbers]

    if relevant_matches:
        matches_html = "<ul class='patents'>" + "".join(_render_patent_item(m) for m in relevant_matches) + "</ul>"
    elif all_matches:
        matches_html = "<p class='empty-inline'>검색은 됐지만, 실제로 관련성이 높다고 판단된 특허는 없습니다.</p>"
    else:
        matches_html = "<p class='empty-inline'>참고한 특허가 없습니다.</p>"

    if other_matches:
        raw_items = "".join(_render_patent_item(m) for m in other_matches)
        matches_html += (
            f"<details style='margin-top:12px;'>"
            f"<summary style='cursor:pointer; font-size:13px; color:#666;'>"
            f"검색은 됐지만 관련성 낮다고 판단된 특허 더보기 ({len(other_matches)}건)</summary>"
            f"<ul class='patents'>{raw_items}</ul></details>"
        )

    license_review = r.get("license_review") or []
    license_html = ""
    if license_review:
        lic_items = []
        for lic in license_review:
            lic_risk = lic.get("risk") or "주의"
            lic_bg, lic_text = ("#e3f5e9", "#1a7f37") if lic_risk == "안전" else ("#fdf1de", "#b25e09")
            lic_name = lic.get("name") or "(이름 없음)"
            lic_version = lic.get("version") or ""
            lic_license = lic.get("license") or "확인 불가"
            lic_note = lic.get("note") or ""
            lic_items.append(
                "<li style='display:flex; align-items:flex-start; justify-content:space-between; "
                "gap:12px; padding:14px 0; border-bottom:0.5px solid #e5e5e5;'>"
                f"<div style='flex:1;'><p style='font-size:14px; font-weight:500; margin:0;'>{lic_name} "
                f"<span style='color:#888; font-weight:400;'>{lic_version} · {lic_license}</span></p>"
                f"<p style='font-size:13px; color:#666; margin:4px 0 0;'>{lic_note}</p></div>"
                f"<span class='badge' style='background:{lic_bg}; color:{lic_text};'>{lic_risk}</span></li>"
            )
        license_html = (
            "<div style='border-top:2px solid #e5e5e5; margin:36px 0 0;'></div>"
            "<h2>라이선스 검토</h2>"
            "<p style='font-size:13px; color:#888; margin:0 0 12px;'>"
            "이번 변경사항에서 새로 추가된 라이브러리를 확인했습니다. 특허 위험도와는 별개의 판단입니다.</p>"
            f"<ul style='list-style:none; padding:0; margin:0;'>{''.join(lic_items)}</ul>"
        )

    if risk not in ("medium", "high"):
        status_html = ""
    elif status == "resolved":
        status_html = "<span class='badge' style='background:#e3f5e9; color:#1a7f37;'>해결됨</span>"
    else:
        status_html = (
            "<span class='badge' style='background:#f0f0f0; color:#666;'>검토 대기</span>"
            f"<form method='post' action='/reports/{report_id}/resolve' style='display:inline; margin-left:8px;'>"
            "<button type='submit'>검토 완료로 표시</button></form>"
        )

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
  <div style="display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:4px;">
    <div style="display:flex; align-items:center; gap:10px;">
      <span class='badge' style='background:{bg}; color:{text};'>{label}</span>
      <span style="font-size:16px; font-weight:500;">{msg}</span>
    </div>
    <div>{status_html}</div>
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
  {license_html}
</body>
</html>"""
    return HTMLResponse(content=html)


@app.post("/reports/{report_id}/resolve")
async def resolve_report(report_id: str):
    mark_resolved(report_id)
    return RedirectResponse(url=f"/reports/{report_id}", status_code=303)


def _extract_changed_files(diff_text: str) -> list[str]:
    """get_commit_diff가 만든 '--- {filename} ---' 마커에서 파일명만 뽑아낸다."""
    return re.findall(r"^--- (.+?) ---$", diff_text, re.MULTILINE)


_REQ_LINE_RE = re.compile(r"^([A-Za-z0-9_.\-]+)(?:\[[\w,\s]+\])?\s*(?:==\s*([\w.\-]+))?")


def _extract_requirements_additions(diff_text: str, filename: str = "requirements.txt") -> list[tuple[str, str | None]]:
    """diff_text에서 requirements.txt 블록만 골라, 새로 추가된(+) 줄에서 패키지명/버전을 뽑는다.

    == 로 버전이 고정된 경우만 버전을 함께 반환하고, 아니면 None (deps.dev가 기본 버전을 찾는다).
    """
    marker = f"--- {filename} ---"
    if marker not in diff_text:
        return []
    start = diff_text.index(marker) + len(marker)
    next_marker_idx = diff_text.find("\n--- ", start)
    block = diff_text[start:next_marker_idx] if next_marker_idx != -1 else diff_text[start:]

    packages: list[tuple[str, str | None]] = []
    for line in block.splitlines():
        if not line.startswith("+") or line.startswith("++"):
            continue
        content = line[1:].strip()
        if not content or content.startswith("#") or content.startswith("-"):
            continue
        m = _REQ_LINE_RE.match(content)
        if not m:
            continue
        packages.append((m.group(1), m.group(2)))
    return packages


async def _process_push_event(info: dict, diff_text: str) -> None:
    if not diff_text.strip():
        logger.info("empty diff, skip: %s/%s @ %s", info["owner"], info["repo"], info["commit_sha"])
        return

    result = await run_pipeline(diff_text)
    changed_files = _extract_changed_files(diff_text)

    license_review: list[dict] = []
    if "requirements.txt" in changed_files:
        new_packages = _extract_requirements_additions(diff_text)
        for name, version in new_packages:
            info_dict = await get_pypi_license(name, version)
            license_review.append(info_dict)
        logger.info("license review: %d개 라이브러리 확인 (%s)", len(license_review), changed_files)

    doc_id = save_report(
        source="github",
        repo_or_doc_id=f"{info['owner']}/{info['repo']}",
        trigger_ref=info["commit_sha"],
        report=result,
        commit_message=info.get("commit_message") or "",
        changed_files=changed_files,
        license_review=license_review,
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
