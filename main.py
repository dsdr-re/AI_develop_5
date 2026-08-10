"""IP Sentinel Cloud Run 엔트리포인트."""

import asyncio
import datetime
import logging
import os
import re
from collections import OrderedDict

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from agents.pipeline import run_pipeline
from services.firestore_store import (
    get_dashboard_stats,
    get_report,
    list_reports,
    mark_pending,
    mark_resolved,
    save_report,
)
from services.github_client import (
    create_webhook,
    extract_push_event_info,
    get_commit_diff,
    get_default_branch,
    get_file_content,
    list_repo_files,
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

_ICON_CHECK = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
    'style="vertical-align:-3px; margin-right:4px;"><path d="M5 12l5 5L20 7"></path></svg>'
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
  .navbar-icon { width: 32px; height: 32px; border-radius: 8px; background: #DBEAFE;
                 display: flex; align-items: center; justify-content: center; color: #2563EB; }
  .navbar-title { font-size: 16px; font-weight: 500; }
  .navbar-links { display: flex; gap: 20px; }
  .navbar-links a { font-size: 14px; color: #666; padding-bottom: 2px; }
  .navbar-links a.active { color: #2563EB; font-weight: 500; border-bottom: 2px solid #2563EB; }
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
  .status-tag { font-size: 12px; color: #1a7f37; background: #e3f5e9; padding: 2px 8px;
                border-radius: 10px; margin-right: 8px; white-space: nowrap; }
  .header-card { background: #EFF6FF; border: 0.5px solid #BFDBFE; border-radius: 12px;
                 padding: 20px 24px; margin-bottom: 28px; }
  .header-title { font-size: 20px; font-weight: 500; margin: 10px 0 6px; }
  .btn-primary { padding: 8px 18px; background: #2563EB; color: white; border: none;
                 border-radius: 8px; font-size: 14px; font-weight: 500; cursor: pointer; }
  .btn-status-pending { display: inline-flex; align-items: center; padding: 8px 14px;
                        background: white; color: #2563EB; border: 1px solid #2563EB;
                        border-radius: 8px; font-size: 14px; font-weight: 500; cursor: pointer;
                        white-space: nowrap; }
  .btn-status-resolved { display: inline-flex; align-items: center; padding: 8px 14px;
                         background: #e3f5e9; color: #1a7f37; border: none;
                         border-radius: 8px; font-size: 14px; font-weight: 500; cursor: pointer;
                         white-space: nowrap; }
  .ref-num { width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center;
             justify-content: center; font-size: 13px; font-weight: 500; flex-shrink: 0; }
"""


def _nav_html(active: str) -> str:
    dash_cls = "active" if active == "dashboard" else ""
    list_cls = "active" if active == "list" else ""
    connect_cls = "active" if active == "connect" else ""
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
    <a href="/connect" class="{connect_cls}">저장소 연결</a>
  </div>
</div>
"""


def _icon_for(filename: str) -> str:
    return _ICON_CODE if any(filename.endswith(ext) for ext in _CODE_EXTENSIONS) else _ICON_DOC


_KST = datetime.timezone(datetime.timedelta(hours=9))


def _to_kst(dt: datetime.datetime | None) -> datetime.datetime | None:
    """Firestore는 UTC로 저장되므로, 화면에 보여줄 때는 한국 시간(KST, UTC+9)으로 변환한다."""
    if not dt:
        return None
    return dt.astimezone(_KST)


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
    dt_kst = _to_kst(dt)
    return dt_kst.strftime("%Y-%m-%d") if dt_kst else ""


def _date_bucket(dt: datetime.datetime | None) -> str:
    dt_kst = _to_kst(dt)
    if not dt_kst:
        return "날짜 미상"
    now_kst = datetime.datetime.now(_KST)
    today = now_kst.date()
    d = dt_kst.date()
    if d == today:
        return "오늘"
    if d == today - datetime.timedelta(days=1):
        return "어제"
    if (today - d).days < 7:
        return "이번 주"
    return dt_kst.strftime("%Y-%m-%d")


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
            created = _to_kst(r.get("created_at"))
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
    created = _to_kst(r.get("created_at"))
    created_str = created.strftime("%Y-%m-%d %H:%M") if created else ""
    status = r.get("status") or "pending"

    extracted = r.get("extracted_context")
    summary = extracted.get("summary") if isinstance(extracted, dict) else ""

    risk_assessment = r.get("risk_assessment")
    ra = risk_assessment if isinstance(risk_assessment, dict) else {}
    # 구 스키마(rationale/related_patents)로 저장된 예전 리포트도 안 죽게 폴백 처리
    intro = ra.get("intro") or ra.get("rationale") or ""
    patent_reasons = ra.get("patent_reasons") or []
    closing_note = ra.get("closing_note") or ""
    recommended_action = ra.get("recommended_action") or ""

    patent_results = r.get("patent_search_results")
    all_matches = (patent_results.get("matches") or []) if isinstance(patent_results, dict) else []
    matches_by_appno = {m.get("application_number"): m for m in all_matches if m.get("application_number")}

    # patent_reasons 순서대로 번호(①②③)를 매기고, 실제 matches에 있는 것만 유효하게 취급
    numbered = []
    for pr in patent_reasons:
        app_no = pr.get("application_number") if isinstance(pr, dict) else None
        match = matches_by_appno.get(app_no)
        if match:
            numbered.append((len(numbered) + 1, match, pr.get("reason", "") if isinstance(pr, dict) else ""))

    referenced_appnos = {m.get("application_number") for _, m, _ in numbered}
    other_matches = [m for m in all_matches if m.get("application_number") not in referenced_appnos]

    reason_blocks = []
    for num, match, reason_text in numbered:
        title = match.get("title") or "(제목 없음)"
        reason_blocks.append(
            f"<div style='display:flex; gap:12px; margin-bottom:16px;'>"
            f"<span class='ref-num' style='background:{bg}; color:{text};'>{num}</span>"
            f"<div style='flex:1;'>"
            f"<a href='#patent-{num}' style='font-weight:500; color:#1a1a1a; font-size:14px;'>{title}</a>"
            f"<p style='font-size:14px; color:#444; line-height:1.7; margin:4px 0 0;'>{reason_text}</p>"
            f"</div></div>"
        )

    rationale_parts = []
    if intro:
        rationale_parts.append(f"<p style='font-size:14px; color:#444; line-height:1.7; margin:0 0 16px;'>{intro}</p>")
    rationale_parts.extend(reason_blocks)
    if closing_note:
        rationale_parts.append(f"<p style='font-size:14px; color:#444; line-height:1.7; margin:0;'>{closing_note}</p>")
    rationale_html = "".join(rationale_parts) if rationale_parts else "<p class='empty-inline'>근거 없음</p>"

    def _render_numbered_patent(num: int, m: dict, is_last: bool) -> str:
        title = m.get("title") or "(제목 없음)"
        app_no = m.get("application_number") or "번호 미상"
        p_status = m.get("registration_status") or "상태 미상"
        border = "" if is_last else "border-bottom:0.5px solid #e5e5e5;"
        return (
            f"<div id='patent-{num}' style='display:flex; gap:12px; padding:14px 16px; {border}'>"
            f"<span class='ref-num' style='background:{bg}; color:{text}; margin-top:1px;'>{num}</span>"
            f"<div style='flex:1;'><p style='font-size:14px; font-weight:500; margin:0;'>{title}</p>"
            f"<p style='font-size:12px; color:#888; margin:4px 0 0;'>출원번호 {app_no} · 등록상태 {p_status}</p></div></div>"
        )

    def _render_plain_patent(m: dict) -> str:
        title = m.get("title") or "(제목 없음)"
        app_no = m.get("application_number") or "번호 미상"
        p_status = m.get("registration_status") or "상태 미상"
        note = m.get("relevance_note") or ""
        return (
            f"<li><strong>{title}</strong><br>"
            f"<span class='meta'>출원번호 {app_no} · 등록상태 {p_status}</span>"
            f"<div class='note'>{note}</div></li>"
        )

    if numbered:
        items_html = "".join(_render_numbered_patent(n, m, n == len(numbered)) for n, m, _ in numbered)
        matches_html = f"<div style='border:0.5px solid #e5e5e5; border-radius:12px; overflow:hidden;'>{items_html}</div>"
    elif all_matches:
        matches_html = "<p class='empty-inline'>검색은 됐지만, 실제로 관련성이 높다고 판단된 특허는 없습니다.</p>"
    else:
        matches_html = "<p class='empty-inline'>참고한 특허가 없습니다.</p>"

    if other_matches:
        raw_items = "".join(_render_plain_patent(m) for m in other_matches)
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
        status_html = (
            f"<form method='post' action='/reports/{report_id}/reopen'>"
            f"<button type='submit' class='btn-status-resolved'>{_ICON_CHECK}해결됨</button></form>"
        )
    else:
        status_html = (
            f"<form method='post' action='/reports/{report_id}/resolve'>"
            f"<button type='submit' class='btn-status-pending'>{_ICON_CHECK}검토 완료로 표시</button></form>"
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

  <div class="header-card">
    <div style="display:flex; align-items:flex-start; justify-content:space-between; gap:16px;">
      <div>
        <span class='badge' style='background:{bg}; color:{text};'>{label}</span>
        <p class="header-title">{msg}</p>
        <p class="sub" style="margin:0;">{repo} · {created_str} · {file_label}
          · <a href="{github_url}" target="_blank" rel="noopener">GitHub에서 보기</a></p>
      </div>
      <div>{status_html}</div>
    </div>
  </div>

  <h2>요약</h2>
  <div class="box">{summary or '요약 없음'}</div>

  <h2>근거</h2>
  {rationale_html}

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


@app.post("/reports/{report_id}/reopen")
async def reopen_report(report_id: str):
    mark_pending(report_id)
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


def _format_as_synthetic_diff(filename: str, content: str) -> str:
    """파일 전체 내용을 "전부 새로 추가된 것"처럼 diff 형식으로 포장한다.

    초기 연결 시 "이미 있던 파일"도 같은 파이프라인(특허 검색, 라이선스 검토)에
    그대로 통과시키기 위한 용도 — 실제 diff든 파일 전체든 에이전트 입장에서는
    똑같은 형식의 텍스트로 보인다.
    """
    plus_lines = "\n".join(f"+{line}" for line in content.splitlines())
    return f"--- {filename} ---\n{plus_lines}"


@app.get("/connect", response_class=HTMLResponse)
async def connect_page():
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>저장소 연결</title>
<style>{_PAGE_STYLE}</style>
</head>
<body>
  {_nav_html('connect')}
  <h1>GitHub 저장소 연결</h1>
  <p class="sub">owner/repo 형식으로 입력하세요 (예: dsdr-re/AI_develop_5). 연결하면 웹훅을
    자동으로 등록하고, 이미 저장소에 있던 기획 문서·코드도 한 번 훑어 리포트를 만듭니다.</p>
  <form method="post" action="/connect" style="margin-top:20px; display:flex; gap:8px;">
    <input type="text" name="repo" placeholder="owner/repo" required
      style="padding:8px 12px; width:280px; border:0.5px solid #ccc; border-radius:8px; font-size:14px;">
    <button type="submit" class="btn-primary">연결하기</button>
  </form>
</body>
</html>"""
    return HTMLResponse(content=html)


async def _run_initial_scan(owner: str, repo: str) -> None:
    """저장소를 처음 연결했을 때, 이미 있던 관련 파일(.md/.py/requirements.txt)을
    전부 훑어서 리포트를 만든다. KIPRIS/Gemini 호출량 보호를 위해 파일 수를 제한하고,
    파일 사이에 짧은 텀을 둬서 KIPRIS 서버에 연달아 몰아치지 않게 한다.
    같은 저장소를 다시 연결해도 이미 스캔한 파일은 건너뛴다(중복 방지).
    """
    MAX_FILES = 15
    DELAY_BETWEEN_FILES = 2.0
    repo_id = f"{owner}/{repo}"
    try:
        branch = await get_default_branch(owner, repo)
        files = await list_repo_files(owner, repo, branch=branch)
    except Exception:
        logger.exception("initial scan: failed to list files for %s/%s", owner, repo)
        return

    # 이 저장소에 대해 이미 초기 스캔으로 만들어진 리포트가 있으면 그 파일들은 건너뛴다.
    already_scanned = {
        r.get("trigger_ref")
        for r in list_reports(limit=200)
        if r.get("repo_or_doc_id") == repo_id and (r.get("trigger_ref") or "").startswith("initial-scan:")
    }
    files = [p for p in files if f"initial-scan:{p}" not in already_scanned]

    logger.info(
        "initial scan: %s에서 관련 파일 %d개 발견(이미 스캔한 것 제외), 최대 %d개까지 스캔",
        repo_id, len(files), MAX_FILES,
    )

    scanned = 0
    for path in files:
        if scanned >= MAX_FILES:
            logger.info("initial scan: MAX_FILES(%d) 도달, 나머지 %d개는 건너뜀", MAX_FILES, len(files) - scanned)
            break
        if scanned > 0:
            await asyncio.sleep(DELAY_BETWEEN_FILES)
        try:
            content = await get_file_content(owner, repo, path)
        except Exception:
            logger.exception("initial scan: failed to fetch %s", path)
            continue
        if not content.strip():
            continue

        diff_text = _format_as_synthetic_diff(path, content)
        result = await run_pipeline(diff_text)

        license_review: list[dict] = []
        if path.rsplit("/", 1)[-1] == "requirements.txt":
            for name, version in _extract_requirements_additions(diff_text, filename=path):
                license_review.append(await get_pypi_license(name, version))

        save_report(
            source="github",
            repo_or_doc_id=f"{owner}/{repo}",
            trigger_ref=f"initial-scan:{path}",
            report=result,
            commit_message=f"초기 스캔: {path}",
            changed_files=[path],
            license_review=license_review,
        )
        scanned += 1
        logger.info("initial scan: %s 처리 완료 (%d/%d)", path, scanned, min(len(files), MAX_FILES))

    logger.info("initial scan 완료: %s/%s, 총 %d개 파일 처리", owner, repo, scanned)


@app.post("/connect", response_class=HTMLResponse)
async def connect_repo(request: Request, background_tasks: BackgroundTasks, repo: str = Form(...)):
    repo = repo.strip()
    if "/" not in repo:
        return HTMLResponse(
            content="<p>owner/repo 형식으로 입력해주세요 (예: dsdr-re/AI_develop_5). "
            "<a href='/connect'>다시 시도</a></p>",
            status_code=400,
        )
    owner, _, repo_name = repo.partition("/")

    webhook_url = str(request.base_url).rstrip("/") + "/webhook/github"
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")

    webhook_ok = False
    webhook_already_exists = False
    webhook_error = ""
    try:
        await create_webhook(owner, repo_name, webhook_url, secret)
        webhook_ok = True
    except Exception as exc:
        webhook_error = str(exc)
        if "already exists" in webhook_error.lower():
            webhook_already_exists = True
        else:
            logger.warning("웹훅 자동 등록 실패: %s/%s: %s", owner, repo_name, exc)

    background_tasks.add_task(_run_initial_scan, owner, repo_name)

    if webhook_ok:
        status_box = (
            "<div class='box' style='background:#e3f5e9;'>웹훅이 자동으로 등록됐습니다. "
            "앞으로 이 저장소에 커밋이 생길 때마다 자동으로 분석됩니다.</div>"
        )
    elif webhook_already_exists:
        status_box = (
            "<div class='box' style='background:#e3f5e9;'>이 저장소에는 이미 웹훅이 등록되어 있습니다. "
            "별도로 할 일은 없고, 앞으로도 커밋할 때마다 자동으로 분석됩니다.</div>"
        )
    else:
        status_box = (
            "<div class='box' style='background:#fdf1de;'>웹훅 자동 등록에 실패했습니다 "
            f"({webhook_error}). 아래 정보로 GitHub 저장소 Settings → Webhooks에서 직접 등록해주세요.<br><br>"
            f"<b>Payload URL:</b> {webhook_url}<br>"
            "<b>Content type:</b> application/json<br>"
            "<b>Secret:</b> (Secret Manager의 github-webhook-secret 값)</div>"
        )

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>저장소 연결</title>
<style>{_PAGE_STYLE}</style>
</head>
<body>
  {_nav_html('connect')}
  <h1>{owner}/{repo_name} 연결 처리 중</h1>
  {status_box}
  <p class="sub" style="margin-top:16px;">이미 있던 파일들을 백그라운드에서 스캔하고 있습니다 (최대 15개,
    몇 분 정도 걸릴 수 있습니다). <a href="/reports?view=all">전체 이력에서 진행 확인 →</a></p>
</body>
</html>"""
    return HTMLResponse(content=html)


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
