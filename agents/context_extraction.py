"""Context Extraction Agent: 코드 diff / 기획 문서에서 특허 검색에 쓸 핵심 기술 요소를 뽑아낸다.

입력: session.state["raw_diff_or_doc"] (Orchestrator가 GitHub/Drive에서 가져온 원문)
출력: output_key="extracted_context" (다음 에이전트가 {extracted_context}로 참조)
"""

import os

from google.adk.agents import LlmAgent

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

context_extraction_agent = LlmAgent(
    name="ContextExtractionAgent",
    model=MODEL,
    description="코드 diff 또는 기획 문서에서 특허 검색에 필요한 핵심 기술 요소를 추출한다.",
    instruction="""당신은 소프트웨어 코드와 기획 문서를 읽고, 특허 조사를 위한 핵심 기술 요소를
뽑아내는 전문가입니다.

다음 원문을 분석하세요:
---
{raw_diff_or_doc}
---

작업:
1. 이 변경사항/문서에 담긴 "새로운 아이디어" 또는 "핵심 기술 요소"를 1~3개 식별한다.
   (단순 리팩터링, 오타 수정, 스타일 변경 등 특허와 무관한 변경은 제외)
2. 각 요소를 특허 검색에 쓸 수 있는 짧은 한국어 키워드(명사구, 5어절 이내)로 표현한다.
3. 특허 위험과 무관하다고 판단되면 이유와 함께 빈 목록을 반환한다.

다음 JSON 형식으로만 출력하세요 (설명 문구 없이):
{{
  "has_patent_relevant_change": true|false,
  "keywords": ["키워드1", "키워드2"],
  "summary": "이 변경사항이 무엇을 하는지 한 문장 요약"
}}
""",
    output_key="extracted_context",
)
