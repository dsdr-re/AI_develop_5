"""IP Sentinel 파이프라인: Context Extraction → Patent Search → Risk Assessment → Reporter.

Orchestrator 역할은 이 파이프라인을 언제/무엇으로 트리거할지 결정하는 main.py의
웹훅 핸들러가 담당한다 (ADK의 SequentialAgent가 순서 보장 자체는 대신 해주므로,
별도의 "Orchestrator 에이전트" 클래스를 두지 않고 트리거 로직으로 구현).
"""

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
    """output_schema 없이 텍스트로 출력된 에이전트 결과(JSON처럼 생긴 문자열)를
    실제 딕셔너리로 변환한다. Gemini가 지시를 어기고 코드펜스(```json)로 감싸는
    경우도 있어 먼저 벗겨낸다. 파싱에 실패하면 원본 문자열을 그대로 반환한다
    (호출부에서 dict가 아닐 수 있음을 방어적으로 처리해야 함).
    """
    if not isinstance(value, str):
        return value
    text = _CODE_FENCE_RE.sub("", value.strip()).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return value


def _reconcile_patent_matches(patent_search_results: dict, raw_kipris_calls: list[dict]) -> dict:
    """LLM이 도구 결과를 텍스트로 다시 써내는 과정에서 일부 항목을 누락시킬 수 있다는 게
    실제로 확인됐다 (관련 있어 보이는 특허를 스스로 판단해서 빼버림). 그래서 최종 matches는
    LLM의 요약을 신뢰하지 않고, run_async 이벤트 스트림에서 직접 수집한 도구의 실제 반환값을
    authoritative source로 삼아 재구성한다. LLM이 만든 relevance_note는 참고용으로만 재사용한다.
    """
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


def _enforce_search_failed_risk(risk_assessment: dict, patent_search_results: dict) -> dict:
    """검색이 실패했는데도 위험도가 '낮음'으로 나오는 경우가 실제로 확인됐다 — LLM이
    프롬프트의 규칙("검색 실패 시 낮음으로 판단 금지")을 항상 지키는 게 아니다.
    patent_search_results['search_failed']는 코드가 실제 도구 호출 결과를 보고 직접
    계산한 값(신뢰 가능)이므로, 이 값을 기준으로 risk_level을 코드에서 강제 보정한다.
    """
    if not isinstance(risk_assessment, dict):
        risk_assessment = {}
    if isinstance(patent_search_results, dict) and patent_search_results.get("search_failed"):
        if risk_assessment.get("risk_level") == "low":
            risk_assessment["risk_level"] = "medium"
            risk_assessment["rationale"] = (
                "KIPRIS 검색 중 일부 또는 전체가 실패해 완전한 검색 결과를 확인하지 못했습니다. "
                "검색 결과가 없다는 뜻이 아니라 확인 자체를 못 한 상태이므로, 안전하다고 판단할 수 없습니다."
            )
            risk_assessment["recommended_action"] = (
                "KIPRIS 검색을 다시 시도하거나(웹훅 Redeliver), 직접 KIPRIS Plus에서 검색해 확인해 주세요."
            )
    return risk_assessment


async def run_pipeline(raw_diff_or_doc: str, *, user_id: str = "system") -> dict:
    """diff/문서 원문을 받아 파이프라인 전체를 실행하고 최종 상태를 반환한다.

    Returns:
        dict: {"final_report": str, "risk_assessment": dict, "patent_search_results": dict,
               "extracted_context": dict}
    """
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

    risk_assessment = _maybe_parse_json(state.get("risk_assessment"))
    risk_assessment = _enforce_search_failed_risk(risk_assessment, patent_search_results)

    return {
        "extracted_context": _maybe_parse_json(state.get("extracted_context")),
        "patent_search_results": patent_search_results,
        "risk_assessment": risk_assessment,
        "final_report": state.get("final_report"),  # 마크다운 텍스트이므로 파싱하지 않음
    }
