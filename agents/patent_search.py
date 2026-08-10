"""Patent Search Agent: 추출된 키워드로 KIPRIS Plus를 검색해 유사 특허 후보를 찾는다."""

import logging
import os

from google.adk.agents import LlmAgent

from services.kipris_client import KiprisClientError, search_patents

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
logger = logging.getLogger("ip-sentinel.patent_search")


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
        logger.warning("KIPRIS search failed for %r: %s", keyword, exc)
        return {"error": str(exc), "keyword": keyword, "results": []}

    logger.info("KIPRIS search %r -> %d results", keyword, len(results))

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
    instruction="""당신은 특허 조사 보조 에이전트입니다. 당신의 역할은 검색과 "기계적으로 그대로
옮겨적기"이지, 관련성을 판단해서 걸러내는 것이 아닙니다. 관련성 평가는 다음 단계(Risk
Assessment 에이전트)의 역할입니다.

이전 단계에서 추출된 컨텍스트:
---
{extracted_context}
---

작업:
1. extracted_context의 "has_patent_relevant_change"가 false이면, 검색 없이
   {{"searched": false, "search_failed": false, "reason": "특허 관련 변경사항 없음", "matches": []}}만 출력하세요.
2. true라면, "keywords" 배열의 각 키워드에 대해 search_kipris 도구를 호출하세요.
3. 도구 결과에 "error" 필드가 있으면 그 키워드는 검색 실패로 기록하세요. 검색 실패는
   "위험 없음"이 절대 아닙니다 — 검색을 못 한 것이지, 특허가 존재하지 않는다는 뜻이
   아닙니다. 이 구분을 반드시 지키세요.
4. **중요**: search_kipris 도구가 "results" 배열에 항목을 반환했다면, 그 항목들을
   전부(하나도 빠짐없이) 최종 matches 배열에 포함시키세요. 스스로 "이건 관련 없어
   보인다"고 판단해서 임의로 제외하지 마세요. 도구가 실제로 준 결과를 누락시키는 것은
   심각한 오류입니다. matches가 비어야 하는 경우는 오직 도구의 "results"가 실제로
   빈 배열이었을 때뿐입니다.
5. 모든 검색 결과를 종합해 다음 JSON으로만 출력하세요 (설명 문구 없이):
{{
  "searched": true,
  "search_failed": true|false,
  "queries": ["검색에 사용한 키워드들"],
  "matches": [
    {{
      "application_number": "...",
      "title": "...",
      "applicant": "...",
      "abstract_snippet": "...",
      "registration_status": "KIPRIS가 반환한 등록상태 값 그대로 (예: 등록/공개/소멸/거절/취하)",
      "relevance_note": "이 특허가 이 검색 키워드와 왜 함께 나왔는지 한 문장 (관련 없다고 판단해도 일단 적으세요)"
    }}
  ]
}}
검색에 실패한 키워드가 하나라도 있으면 최상위 "search_failed"를 true로 표시하세요.
특허 내용을 지어내지 말고, 도구가 실제로 반환한 결과와 registration_status 값만
그대로 사용하세요.
""",
    tools=[search_kipris],
    output_key="patent_search_results",
)
