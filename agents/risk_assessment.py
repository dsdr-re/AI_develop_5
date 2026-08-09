"""Risk Assessment Agent: 특허 검색 결과를 근거로 위험도를 판단한다.

입력: {extracted_context}, {patent_search_results}
출력: output_key="risk_assessment" (구조화된 JSON, output_schema로 강제)

설계 원칙 (인터뷰 UR-02에서 확인됨):
    "위험합니다" 한 마디로 끝내지 않는다. 위험도 등급 + 근거 + 다음 행동을
    반드시 함께 준다. 특허 전문가가 아닌 사용자도 판단할 수 있어야 한다.
"""

import os
from typing import Literal

from google.adk.agents import LlmAgent
from pydantic import BaseModel, Field

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


class RiskAssessmentOutput(BaseModel):
    risk_level: Literal["low", "medium", "high"] = Field(
        description="low=참고만 해도 됨, medium=주의 깊게 검토 필요, high=변리사 상담 권장"
    )
    rationale: str = Field(description="이 위험도로 판단한 근거 (관련 특허와의 유사점 등)")
    recommended_action: str = Field(description="사용자가 다음에 취해야 할 구체적 행동")
    related_patents: list[str] = Field(
        default_factory=list, description="근거로 사용된 특허의 출원번호 목록"
    )


risk_assessment_agent = LlmAgent(
    name="RiskAssessmentAgent",
    model=MODEL,
    description="특허 검색 결과를 바탕으로 위험도(상/중/하), 근거, 권장 행동을 판단한다.",
    instruction="""당신은 IP 리스크 평가 전문가입니다. 특허 전문 지식이 없는 개발자·창업자도
이해할 수 있도록 판단 근거와 다음 행동을 명확히 제시해야 합니다.

컨텍스트:
---
{extracted_context}
---

특허 검색 결과:
---
{patent_search_results}
---

규칙:
- patent_search_results.searched가 false이거나 matches가 비어있으면 risk_level="low",
  rationale에 "관련 특허가 검색되지 않음"이라고 명시하세요.
- 청구항 전체를 보지 못한 상태이므로, high 판정은 제목·초록 수준에서도 명백히 겹치는
  경우로 제한하고 "정식 청구항 대조는 변리사 상담으로 확인 필요"라고 rationale에 남기세요.
- 추측이나 확정적 법률 판단(예: "이것은 특허 침해입니다")은 하지 마세요. IP Sentinel은
  1차 스크리닝 도구이지 법률 자문이 아닙니다.
""",
    output_schema=RiskAssessmentOutput,
    output_key="risk_assessment",
)
