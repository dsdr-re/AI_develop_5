"""Patent Search Agent: 추출된 키워드로 KIPRIS Plus를 검색해 유사 특허 후보를 찾는다.

입력: {extracted_context} (Context Extraction 에이전트의 출력)
출력: output_key="patent_search_results"
"""

import json
import os

from google.adk.agents import LlmAgent

from services.kipris_client import KiprisClientError, search_patents

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


async def search_kipris(keyword: str) -> dict:
    """KIPRIS Plus에서 키워드로 특허·실용신안을 검색한다.

    Args:
        keyword: 검색할 기술 키워드 (한국어 명사구 권장, 예: "잠금화면 광고 모듈")

    Returns:
        검색된 특허 후보 목록과 총 건수를 담은 딕셔너리.
        실패 시 error 필드에 사유가 담긴다.
    """
    try:
        results = await search_patents(keyword, num_rows=10)
    except KiprisClientError as exc:
        return {"error": str(exc), "keyword": keyword, "results": []}

    return {
        "keyword": keyword,
        "count": len(results),
        "results": [
            {
                "application_number": r.application_number,
                "title": r.title,
                "applicant": r.applicant,
                "abstract": (r.abstract or "")[:300],
                "registration_status": r.registration_status,
            }
            for r in results
        ],
    }


patent_search_agent = LlmAgent(
    name="PatentSearchAgent",
    model=MODEL,
    description="추출된 키워드로 KIPRIS Plus를 검색해 유사 특허 후보를 찾는다.",
    instruction="""당신은 특허 조사 보조 에이전트입니다.

이전 단계에서 추출된 컨텍스트:
---
{extracted_context}
---

작업:
1. extracted_context의 "has_patent_relevant_change"가 false이면, 검색 없이
   {{"searched": false, "reason": "특허 관련 변경사항 없음", "matches": []}}만 출력하세요.
2. true라면, "keywords" 배열의 각 키워드에 대해 search_kipris 도구를 호출하세요.
3. 모든 검색 결과를 종합해 다음 JSON으로만 출력하세요 (설명 문구 없이):
{{
  "searched": true,
  "queries": ["검색에 사용한 키워드들"],
  "matches": [
    {{
      "application_number": "...",
      "title": "...",
      "applicant": "...",
      "abstract_snippet": "...",
      "relevance_note": "이 특허가 왜 관련 있어 보이는지 한 문장"
    }}
  ]
}}
검색 결과가 없으면 matches를 빈 배열로 두세요. 특허 내용을 지어내지 말고,
도구가 실제로 반환한 결과만 사용하세요.
""",
    tools=[search_kipris],
    output_key="patent_search_results",
)
