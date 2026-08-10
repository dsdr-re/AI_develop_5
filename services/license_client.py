"""deps.dev API를 이용한 오픈소스 라이브러리 라이선스 조회.

API 문서: https://docs.deps.dev/api/v3/ (공개 API, 인증 불필요)

판정 방식은 Gemini가 아니라 고정된 규칙표 대조다 — 라이선스 이름이 정해져 있고
어떤 게 "공개 의무 계열(copyleft)"인지도 이미 알려진 사실이라, AI 판단이 필요 없다.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("ip-sentinel.license_client")

DEPS_DEV_BASE_URL = "https://api.deps.dev/v3"

# 공개 의무(copyleft) 계열 — 이 코드를 포함하면 우리 프로젝트도 공개해야 할 수 있음
_CAUTION_LICENSE_PREFIXES = ("GPL", "AGPL", "LGPL", "MPL", "EUPL", "OSL", "CPL", "SSPL")


def classify_license(license_str: str) -> tuple[str, str]:
    """라이선스 문자열을 ("안전"|"주의", 설명)으로 분류한다."""
    if not license_str:
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
    return "안전", f"{license_str} 라이선스로, 저작권 표시만 유지하면 자유롭게 이용할 수 있습니다."


async def get_pypi_license(package_name: str, version: str | None = None) -> dict:
    """PyPI 패키지의 라이선스 정보를 deps.dev에서 조회한다.

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
            return {
                "name": package_name,
                "version": version,
                "license": license_str or "확인 불가",
                "risk": risk,
                "note": note,
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
