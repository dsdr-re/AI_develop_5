"""KIPRIS Plus (plus.kipris.or.kr) 특허·실용신안 워드 검색 API 클라이언트.

엔드포인트: patUtiModInfoSearchSevice/getWordSearch
- 인증: 쿼리 파라미터 `ServiceKey`
- 응답 형식: XML
- 무료 등급: 월 1,000회 호출

주의:
    KIPRIS Plus 포털이 이 개발 환경 네트워크에서 접근 불가능해 실제 응답 XML로
    필드명을 검증하지 못했습니다. 아래 FIELD 후보들은 KIPRIS Open API 계열에서
    통상 쓰이는 이름을 기준으로 작성했으니, 처음 실행한 뒤 raw_fields로 실제
    태그명을 확인하고 KNOWN_FIELDS를 필요시 조정하세요.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

KIPRIS_BASE_URL = (
    "https://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice/getWordSearch"
)

# 실제 응답에서 자주 쓰이는 후보 태그명 (확인되는 대로 갱신)
KNOWN_FIELDS = {
    "application_number": ["applicationNumber", "applicationNo"],
    "title": ["inventionTitle", "inventionName"],
    "applicant": ["applicantName", "applicant"],
    "abstract": ["astrtCont", "abstract"],
    "publication_number": ["publicationNumber", "publicationNo"],
    "registration_status": ["registerStatus", "registrationStatus"],
    "application_date": ["applicationDate"],
}


@dataclass
class PatentSearchResult:
    application_number: str | None
    title: str | None
    applicant: str | None
    abstract: str | None
    publication_number: str | None
    registration_status: str | None
    application_date: str | None
    raw_fields: dict = field(default_factory=dict)


class KiprisClientError(RuntimeError):
    pass


def _first_present(raw: dict, candidates: list[str]) -> str | None:
    for key in candidates:
        if key in raw and raw[key]:
            return raw[key]
    return None


def _parse_items(xml_text: str) -> list[dict]:
    """<item> 반복 노드를 태그명: 텍스트 딕셔너리 리스트로 변환 (필드명 무관하게 동작)."""
    root = ET.fromstring(xml_text)
    items = root.findall(".//item")
    parsed = []
    for item in items:
        raw = {child.tag: (child.text or "").strip() for child in item}
        parsed.append(raw)
    return parsed


async def search_patents(
    keyword: str,
    *,
    include_utility_model: bool = True,
    num_rows: int = 20,
    page_no: int = 1,
    service_key: str | None = None,
    timeout: float = 15.0,
) -> list[PatentSearchResult]:
    """키워드로 특허·실용신안을 검색한다.

    Args:
        keyword: 검색할 기술/아이디어 키워드 (한글 또는 영문)
        include_utility_model: 실용신안 포함 여부
        num_rows: 페이지당 결과 수
        page_no: 페이지 번호
        service_key: 지정하지 않으면 환경변수 KIPRIS_PLUS_SERVICE_KEY 사용

    Raises:
        KiprisClientError: 서비스 키가 없거나 요청/파싱에 실패한 경우
    """
    key = service_key or os.environ.get("KIPRIS_PLUS_SERVICE_KEY")
    if not key:
        raise KiprisClientError(
            "KIPRIS_PLUS_SERVICE_KEY가 설정되지 않았습니다. .env 또는 Secret Manager를 확인하세요."
        )

    params = {
        "word": keyword,
        "patent": "true",
        "utility": "true" if include_utility_model else "false",
        "numOfRows": str(num_rows),
        "pageNo": str(page_no),
        "ServiceKey": key,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(KIPRIS_BASE_URL, params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise KiprisClientError(f"KIPRIS Plus 요청 실패: {exc}") from exc

    try:
        raw_items = _parse_items(resp.text)
    except ET.ParseError as exc:
        raise KiprisClientError(
            f"KIPRIS Plus 응답 XML 파싱 실패 (응답 앞부분: {resp.text[:200]!r})"
        ) from exc

    results = []
    for raw in raw_items:
        results.append(
            PatentSearchResult(
                application_number=_first_present(raw, KNOWN_FIELDS["application_number"]),
                title=_first_present(raw, KNOWN_FIELDS["title"]),
                applicant=_first_present(raw, KNOWN_FIELDS["applicant"]),
                abstract=_first_present(raw, KNOWN_FIELDS["abstract"]),
                publication_number=_first_present(raw, KNOWN_FIELDS["publication_number"]),
                registration_status=_first_present(raw, KNOWN_FIELDS["registration_status"]),
                application_date=_first_present(raw, KNOWN_FIELDS["application_date"]),
                raw_fields=raw,
            )
        )
    return results
