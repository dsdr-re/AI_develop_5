"""IP Sentinel 파이프라인: Context Extraction → Patent Search → Risk Assessment → Reporter."""

import json
import re
import uuid

from google.adk.agents import SequentialAgent
from google.adk.runners import InMemoryRunner
from google.genai import types

from agents.context_extraction import context_extraction_agent
from agents.patent_search import patent_search_agent
from agents.reporter import reporter_agent
from agents.risk_assessment import risk_assessment_agent

APP_NAME = "ip-sentinel"

ip_sentinel_pipeline = SequentialAgent(
    name="IPSentinelPipeline",
    description="워크스페이스 변경사항을 받아 특허 리스크를 평가하고 리포트를 생성하는 파이프라인",
    sub_agents=[
        context_extraction_agent,
        patent_search_agent,
        risk_assessment_agent,
        reporter_agent,
    ],
)

_runner = InMemoryRunner(agent=ip_sentinel_pipeline, app_name=APP_NAME)

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


def _maybe_parse_json(value):
    if not isinstance(value, str):
        return value
    text = _CODE_FENCE_RE.sub("", value.strip()).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return value


def _reconcile_patent_matches(patent_search_results: dict, raw_kipris_calls: list[dict]) -> dict:
    """LLM 요약을 신뢰하지 않고, 도구가 실제로 반환한 값을 authoritative source로 삼는다."""
    if not isinstance(patent_search_results, dict):
        patent_search_results = {}

    llm_notes = {}
    for m in patent_search_results.get("matches") or []:
        if isinstance(m, dict) and m.get("application_number"):
            llm_notes[m["application_number"]] = m.get("relevance_note", "")

    seen = set()
    reconciled = []
    any_error = False
    for call in raw_kipris_calls:
        if not isinstance(call, dict):
            continue
        if call.get("error"):
            any_error = True
            continue
        for r in call.get("results") or []:
            app_no = r.get("application_number")
            if not app_no or app_no in seen:
                continue
            seen.add(app_no)
            reconciled.append(
                {
                    "application_number": app_no,
                    "title": r.get("title"),
                    "applicant": r.get("applicant"),
                    "abstract_snippet": (r.get("abstract") or "")[:300],
                    "registration_status": r.get("registration_status"),
                    "relevance_note": llm_notes.get(app_no, ""),
                }
            )

    patent_search_results["matches"] = reconciled
    patent_search_results["searched"] = bool(raw_kipris_calls) or patent_search_results.get("searched", False)
    patent_search_results["search_failed"] = any_error or bool(patent_search_results.get("search_failed"))
    return patent_search_results


async def run_pipeline(raw_diff_or_doc: str, *, user_id: str = "system") -> dict:
    session_id = str(uuid.uuid4())
    session = await _runner.session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
        state={"raw_diff_or_doc": raw_diff_or_doc},
    )

    trigger_message = types.Content(
        role="user", parts=[types.Part.from_text(text="워크스페이스 변경사항을 분석해 주세요.")]
    )

    raw_kipris_calls: list[dict] = []

    async for event in _runner.run_async(
        user_id=user_id, session_id=session.id, new_message=trigger_message
    ):
        for fr in event.get_function_responses():
            if fr.name == "search_kipris" and fr.response:
                raw_kipris_calls.append(fr.response)

    final_session = await _runner.session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session.id
    )
    state = final_session.state

    patent_search_results = _maybe_parse_json(state.get("patent_search_results"))
    patent_search_results = _reconcile_patent_matches(patent_search_results, raw_kipris_calls)

    return {
        "extracted_context": _maybe_parse_json(state.get("extracted_context")),
        "patent_search_results": patent_search_results,
        "risk_assessment": _maybe_parse_json(state.get("risk_assessment")),
        "final_report": state.get("final_report"),
    }
