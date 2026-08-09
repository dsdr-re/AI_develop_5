"""IP Sentinel 파이프라인: Context Extraction → Patent Search → Risk Assessment → Reporter.

Orchestrator 역할은 이 파이프라인을 언제/무엇으로 트리거할지 결정하는 main.py의
웹훅 핸들러가 담당한다 (ADK의 SequentialAgent가 순서 보장 자체는 대신 해주므로,
별도의 "Orchestrator 에이전트" 클래스를 두지 않고 트리거 로직으로 구현).
"""

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

    # SequentialAgent는 각 sub_agent의 instruction 템플릿({raw_diff_or_doc} 등)을
    # session.state에서 채워 넣으므로, 첫 트리거 메시지는 비워도 무방하다.
    trigger_message = types.Content(
        role="user", parts=[types.Part.from_text(text="워크스페이스 변경사항을 분석해 주세요.")]
    )

    async for _event in _runner.run_async(
        user_id=user_id, session_id=session.id, new_message=trigger_message
    ):
        pass  # 중간 이벤트는 로깅용으로만 쓰고, 최종 상태는 session.state에서 읽는다

    final_session = await _runner.session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session.id
    )
    state = final_session.state
    return {
        "extracted_context": state.get("extracted_context"),
        "patent_search_results": state.get("patent_search_results"),
        "risk_assessment": state.get("risk_assessment"),
        "final_report": state.get("final_report"),
    }
