"""deps.dev API를 이용한 오픈소스 라이브러리 라이선스 조회.

API 문서: https://docs.deps.dev/api/v3/ (공개 API, 인증 불필요)

판정 방식은 Gemini가 아니라 고정된 규칙표 대조다 — 라이선스 이름이 정해져 있고
어떤 게 "공개 의무 계열(copyleft)"인지도 이미 알려진 사실이라, AI 판단이 필요 없다.
"""

from __future__ import annotations

import logging

import httpx

from services.license_knowledge import retrieve_license_doc

logger = logging.getLogger("ip-sentinel.license_client")

DEPS_DEV_BASE_URL = "https://api.deps.dev/v3"
PYPI_BASE_URL = "https://pypi.org/pypi"

# 공개 의무(copyleft) 계열 — 이 코드를 포함하면 우리 프로젝트도 공개해야 할 수 있음.
# AGPL/LGPL을 GPL보다 먼저 검사해야 한다 — "AGPL-3.0"이나 "LGPL-3.0" 문자열 안에도
# "GPL"이 부분 문자열로 포함돼 있어서, GPL을 먼저 검사하면 AGPL 전용 경고(서버 제공만
# 해도 공개 의무 발생)를 못 띄우고 일반 GPL 문구로 잘못 안내하게 된다.
_CAUTION_LICENSE_PREFIXES = ("AGPL", "LGPL", "GPL", "MPL", "EUPL", "OSL", "CPL", "SSPL")

# 명확히 안전한 것으로 알려진 permissive 라이선스만 화이트리스트로 관리한다.
# 여기에도 _CAUTION_LICENSE_PREFIXES에도 안 걸리는 문자열(예: "Other", "UNKNOWN",
# 오탈자, 회사 자체 라이선스명 등)은 "안전"으로 낙관하지 않고 "주의"로 고정한다.
_KNOWN_SAFE_LICENSE_PREFIXES = (
    "MIT", "BSD", "APACHE", "ISC", "PYTHON-2.0", "PSF", "UNLICENSE", "0BSD", "ZLIB", "WTFPL",
)


def classify_license(license_str: str) -> tuple[str, str]:
    """라이선스 문자열을 ("안전"|"주의", 설명)으로 분류한다.

    화이트리스트(안전)·블랙리스트(주의) 둘 다에 안 걸리는 비표준/불명확한 표기는
    절대 "안전"으로 격상하지 않고 "주의"로 고정한다.
    """
    if not license_str or not license_str.strip():
        return "주의", "라이선스 정보를 확인할 수 없습니다. 직접 확인이 필요합니다."
    upper = license_str.upper()
    for prefix in _CAUTION_LICENSE_PREFIXES:
        if prefix in upper:
            if prefix == "AGPL":
                return (
                    "주의",
                    f"{license_str} 라이선스는 서버로 제공하기만 해도(배포 없이도) "
                    "소스 공개 의무가 생길 수 있습니다.",
                )
            return (
                "주의",
                f"{license_str} 라이선스는 이 코드를 포함한 프로젝트 전체를 공개해야 할 의무가 생길 수 있습니다.",
            )
    for prefix in _KNOWN_SAFE_LICENSE_PREFIXES:
        if prefix in upper:
            return "안전", f"{license_str} 라이선스로, 저작권 표시만 유지하면 자유롭게 이용할 수 있습니다."
    return (
        "주의",
        f"'{license_str}'는 표준 라이선스 표기로 인식되지 않아 자동 판정이 불가합니다. 직접 확인이 필요합니다.",
    )


async def _fetch_pypi_classifiers(package_name: str, version: str) -> list[str]:
    """deps.dev의 license 필드가 비어있거나 비표준일 때, PyPI 공식 JSON API의
    Trove classifiers를 보조로 조회한다 (예: 'License :: OSI Approved ::
    GNU Lesser General Public License v3 (LGPLv3)').
    """
    url = f"{PYPI_BASE_URL}/{package_name}/{version}/json"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return data.get("info", {}).get("classifiers") or []
        except httpx.HTTPError:
            logger.warning("PyPI classifiers 조회 실패: %s %s", package_name, version)
            return []


def _license_from_classifiers(classifiers: list[str]) -> str:
    """'License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)' 형태의
    classifier에서 라이선스 이름만 뽑아낸다. 여러 개면 가장 구체적인(가장 긴) 것을 쓴다.
    """
    names = [
        c.split("::")[-1].strip()
        for c in classifiers
        if c.startswith("License ::") and c.strip() != "License :: OSI Approved"
    ]
    return max(names, key=len) if names else ""


async def get_pypi_license(package_name: str, version: str | None = None) -> dict:
    """PyPI 패키지의 라이선스 정보를 deps.dev에서 조회하고, 결과가 애매하면
    PyPI classifiers로 보조 조회한다.

    Returns:
        {"name", "version", "license", "risk"("안전"|"주의"), "note"}
        조회 실패 시 risk="주의"(안전 쪽으로 낙관하지 않음), error 필드 포함.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            if not version:
                pkg_resp = await client.get(f"{DEPS_DEV_BASE_URL}/systems/pypi/packages/{package_name}")
                pkg_resp.raise_for_status()
                pkg_data = pkg_resp.json()
                versions = pkg_data.get("versions", [])
                version = None
                for v in versions:
                    if v.get("isDefault"):
                        version = v.get("versionKey", {}).get("version")
                        break
                if not version and versions:
                    version = versions[-1].get("versionKey", {}).get("version")

            if not version:
                return {
                    "name": package_name,
                    "version": None,
                    "license": None,
                    "risk": "주의",
                    "note": "버전 정보를 찾을 수 없어 라이선스를 확인하지 못했습니다.",
                    "error": "no_version_found",
                }

            ver_resp = await client.get(
                f"{DEPS_DEV_BASE_URL}/systems/pypi/packages/{package_name}/versions/{version}"
            )
            ver_resp.raise_for_status()
            ver_data = ver_resp.json()
            licenses = ver_data.get("licenses") or []
            license_str = ", ".join(licenses) if licenses else ""
            risk, note = classify_license(license_str)

            # deps.dev 결과가 비어있거나 비표준으로 판정됐으면 PyPI classifiers로 재시도.
            # classifiers에서 더 구체적인 라이선스명이 나오면 그걸로 다시 판정한다.
            used_classifier_fallback = False
            if risk == "주의" and (not license_str or "표준 라이선스 표기로 인식되지" in note):
                classifiers = await _fetch_pypi_classifiers(package_name, version)
                classifier_license = _license_from_classifiers(classifiers)
                if classifier_license:
                    fallback_risk, fallback_note = classify_license(classifier_license)
                    license_str = classifier_license
                    risk, note = fallback_risk, fallback_note
                    used_classifier_fallback = True

            detailed_obligations = retrieve_license_doc(license_str) if risk == "주의" else ""
            return {
                "name": package_name,
                "version": version,
                "license": license_str or "확인 불가",
                "risk": risk,
                "note": note,
                "detailed_obligations": detailed_obligations,
                "source": "pypi_classifiers" if used_classifier_fallback else "deps.dev",
            }
        except httpx.HTTPError as exc:
            logger.warning("deps.dev 조회 실패: %s: %s", package_name, exc)
            return {
                "name": package_name,
                "version": version,
                "license": None,
                "risk": "주의",
                "note": "라이선스 조회에 실패했습니다. 직접 확인이 필요합니다.",
                "error": str(exc),
            }
