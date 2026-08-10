"""KIPRIS Plus (plus.kipris.or.kr) 특허·실용신안 워드 검색 API 클라이언트."""

from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("ip-sentinel.kipris_client")

KIPRIS_BASE_URL = (
    "https://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice/getWordSearch"
)

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
            logger.exception("KIPRIS Plus 요청 실패: keyword=%r", keyword)
            raise KiprisClientError(f"KIPRIS Plus 요청 실패: {type(exc).__name__}: {exc!r}") from exc
        except Exception as exc:
            logger.exception("KIPRIS Plus 요청 중 예상 못 한 예외: keyword=%r", keyword)
            raise KiprisClientError(f"KIPRIS Plus 요청 중 예상 못 한 오류: {type(exc).__name__}: {exc!r}") from exc

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
