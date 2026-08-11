"""Reporter Agent: 위험도 판단 결과를 사람이 읽을 리포트로 변환한다.

입력: {extracted_context}, {patent_search_results}, {risk_assessment}
출력: output_key="final_report" (마크다운 텍스트 — GitHub PR 코멘트/웹 UI에 그대로 표시 가능)
"""

import os

from google.adk.agents import LlmAgent

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

reporter_agent = LlmAgent(
    name="ReporterAgent",
    model=MODEL,
    description="위험도 판단 결과를 사람이 읽기 쉬운 마크다운 리포트로 변환한다.",
    instruction="""아래 정보를 바탕으로 GitHub 커밋 코멘트 또는 웹 UI에 바로 표시할
마크다운 리포트를 작성하세요.

변경사항 요약: {extracted_context}
특허 검색 결과: {patent_search_results}
위험도 판단: {risk_assessment}

형식 (반드시 이 구조를 따르세요):

## IP DETECDOG 리포트

**위험도:** [🟢 낮음 | 🟡 중간 | 🔴 높음]

**요약:** (risk_assessment.intro)

**근거:**
- (risk_assessment.patent_reasons의 각 항목을 "① [출원번호]: [reason]" 형태로 번호 매겨 나열.
  patent_reasons가 비어있으면 "관련 특허 없음"이라고만 쓰세요)

**검토 포인트:**
- (risk_assessment.recommended_action)

**차별화 포인트:** (risk_assessment.opportunity_note가 비어있지 않을 때만 이 섹션을 포함하세요.
비어있으면 이 섹션 자체를 통째로 생략하세요)

---
*이 리포트는 1차 자동 스크리닝 결과이며 법률 자문이 아닙니다. 위험도가 중간 이상이면
변리사 상담을 권장합니다.*

위 형식 그대로, 다른 설명 없이 마크다운만 출력하세요. 값이 비어서 생략하는 섹션은
헤더까지 통째로 빼고, "없음"이라고 채워 넣지 마세요.
""",
    output_key="final_report",
)
