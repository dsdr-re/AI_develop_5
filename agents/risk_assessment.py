"""Risk Assessment Agent: 특허 검색 결과를 근거로 위험도를 판단한다.

입력: {extracted_context}, {patent_search_results}
출력: output_key="risk_assessment" (구조화된 JSON, output_schema로 강제)

설계 원칙 (인터뷰 UR-02에서 확인됨):
    "위험합니다" 한 마디로 끝내지 않는다. 위험도 등급 + 근거 + 다음 행동을
    반드시 함께 준다. 특허 전문가가 아닌 사용자도 판단할 수 있어야 한다.

근거는 통짜 문장이 아니라 특허별로 나눠 구조화한다 (화면에서 번호(①②)로
각 특허와 대응시켜 보여주기 위함 — 가독성 개선).
"""

import os
from typing import Literal

from google.adk.agents import LlmAgent
from pydantic import BaseModel, Field

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


class PatentReason(BaseModel):
    application_number: str = Field(
        description="이 이유가 설명하는 특허의 출원번호. patent_search_results.matches 안의 값과 정확히 일치해야 함"
    )
    reason: str = Field(description="이 특허가 왜 위험 판단의 근거가 되는지 자세한 설명 (2~3문장, 구체적으로)")


class RiskAssessmentOutput(BaseModel):
    risk_level: Literal["low", "medium", "high"] = Field(
        description="low=참고만 해도 됨, medium=주의 깊게 검토 필요, high=변리사 상담 권장"
    )
    intro: str = Field(description="전체 위험 판단을 한두 문장으로 요약하는 도입부")
    patent_reasons: list[PatentReason] = Field(
        default_factory=list,
        description="위험 판단의 근거로 실제로 사용한 특허별 상세 이유. 관련 특허가 없으면 빈 배열",
    )
    closing_note: str = Field(
        default="", description="마무리 코멘트 (예: 청구항 대조는 변리사 상담 필요, 검색 실패 안내 등)"
    )
    opportunity_note: str = Field(
        default="",
        description="검색된 특허가 아예 없어서 위험도가 낮은 경우, 이를 차별화 기회로 해석하는 한두 문장. "
        "그 외의 경우(특허는 검색됐지만 무관해서 낮음, 검색 실패 등)에는 빈 문자열",
    )
    recommended_action: str = Field(description="사용자가 다음에 취해야 할 구체적 행동")


risk_assessment_agent = LlmAgent(
    name="RiskAssessmentAgent",
    model=MODEL,
    description="특허 검색 결과를 바탕으로 위험도(상/중/하), 특허별 근거, 권장 행동을 판단한다.",
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
- patent_search_results.search_failed가 true이면 KIPRIS 검색이 제대로 되지 않은
  것이므로 risk_level="low"로 판단하면 절대 안 됩니다. risk_level="medium"으로 설정하고,
  intro에 "KIPRIS 검색이 실패해 위험 여부를 확인하지 못함"이라고 명시하세요.
  patent_reasons는 빈 배열로 두고, closing_note에 "재검색 또는 수동 확인 필요"라고
  적으세요. 검색 실패를 "안전함"으로 착각하는 것이 가장 위험한 오류입니다.
- searched가 false이면 (extracted_context가 애초에 특허 관련 아이디어를 찾지
  못해 검색 자체를 하지 않은 경우) risk_level="low", intro는 "이번 변경사항은
  특허 검토 대상이 아닙니다."처럼 짧은 한 문장으로만 쓰고, extracted_context.summary가
  이미 설명한 이유를 다시 풀어서 설명하지 마세요 (화면에 요약과 근거가 나란히 뜨는데
  같은 말을 두 번 하면 안 됩니다). patent_reasons는 빈 배열, opportunity_note는 반드시
  빈 문자열로 두세요 — 애초에 검토할 아이디어가 없었던 것이므로 "차별화 포인트"라고
  부를 대상 자체가 없습니다.
- search_failed가 false이면서 searched가 true인데 matches가 비어있으면 (실제로
  검색은 했는데 겹치는 특허가 하나도 없었던 경우) risk_level="low", intro에 "관련
  특허를 검색했으나 겹치는 항목이 없었습니다"라고 명시하고 patent_reasons는 빈
  배열로 두세요. 이 경우에만 opportunity_note에 "이 아이디어와 겹치는 특허가
  검색되지 않았습니다. 기존 특허들이 다루지 않은 영역일 수 있어, 오히려 차별화
  포인트로 활용할 만한 여지가 있습니다"와 같이 긍정적으로 해석하는 한두 문장을
  추가하세요. (특허가 검색됐지만 무관해서 patent_reasons가 빈 경우나 search_failed인
  경우에는 "차별화됐다"고 확신할 근거가 없으므로 opportunity_note를 빈 문자열로
  두세요.)
- **중요**: patent_search_results.matches는 관련성 필터링을 거치지 않은 원본 검색
  결과입니다. matches에 항목이 있다고 곧바로 위험하다고 판단하지 마세요. 각 항목의
  title·abstract_snippet을 extracted_context의 핵심 아이디어와 직접 비교해서, 실제로
  기술적으로 겹치는 것만 patent_reasons에 포함시키세요. 검색어만 겹치고 실제 내용은
  무관한 특허는 patent_reasons에서 제외하세요 (matches에는 남아있어도 되고, 단지
  patent_reasons에만 넣지 않으면 됩니다).
- **적용 분야가 다르면 무관한 것으로 판단하세요.** "AI/LLM으로 무언가를 감지·평가·
  추출한다"는 상위 개념만 같고 실제 적용 대상(예: 건설현장 안전, 보험 인수 심사,
  CCTV 동선 추적, 뉴스 분석 등)이 extracted_context의 실제 적용 분야(소프트웨어
  개발 워크스페이스의 특허·라이선스 리스크 탐지)와 다르면, patent_reasons에
  포함시키지 마세요. 목적이 아니라 구체적 구성·구현 방식이 겹치는지를 기준으로
  삼으세요. "산업 분야가 다른데 AI 활용 방식만 비슷하다"는 이유로 위험 판단하는
  것이 지금까지 확인된 가장 흔한 오판입니다.
- patent_reasons에 포함시키기로 한 특허 중 registration_status가 "소멸", "거절",
  "취하"인 것은 더 이상 유효한 권리가 아니므로, 그 reason 안에 "이미 [상태] 상태로
  권리가 소멸되었을 가능성이 있음"이라고 반드시 덧붙이고 위험도 판단에서는 비중을
  낮추세요. registration_status가 "등록" 또는 "공개"인 특허를 핵심 근거로 삼으세요.
- patent_reasons의 각 reason은 그 특허의 어떤 구체적인 부분(제목이나 초록의 특정
  구성)이 왜 유사한지 자세히 설명하세요. "관련이 있습니다" 같은 짧은 문장이 아니라,
  실제로 무엇이 겹치는지 근거를 담아 2~3문장으로 작성하세요.
- intro는 전체 판단을 한두 문장으로 요약하고, closing_note는 마무리 코멘트(예: 청구항
  대조는 변리사 상담 필요)를 담으세요. intro와 closing_note에서는 특허를 출원번호로
  지칭하지 마세요 — 출원번호는 patent_reasons의 application_number 필드에만 넣고,
  문장 안에서 번호를 언급하지 마세요 (화면에서 번호(①②)로 자동 표시됩니다).
- 청구항 전체를 보지 못한 상태이므로, high 판정은 제목·초록 수준에서도 명백히 겹치는
  경우로 제한하고 closing_note에 "정식 청구항 대조는 변리사 상담으로 확인 필요"라고
  남기세요.
- 추측이나 확정적 법률 판단(예: "이것은 특허 침해입니다")은 하지 마세요. IP Sentinel은
  1차 스크리닝 도구이지 법률 자문이 아닙니다.
""",
    output_schema=RiskAssessmentOutput,
    output_key="risk_assessment",
)
