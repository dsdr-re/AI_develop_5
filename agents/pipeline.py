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

    async for _event in _runner.run_async(
        user_id=user_id, session_id=session.id, new_message=trigger_message
    ):
        pass

    final_session = await _runner.session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session.id
    )
    state = final_session.state
    return {
        "extracted_context": _maybe_parse_json(state.get("extracted_context")),
        "patent_search_results": _maybe_parse_json(state.get("patent_search_results")),
        "risk_assessment": _maybe_parse_json(state.get("risk_assessment")),
        "final_report": state.get("final_report"),
    }
