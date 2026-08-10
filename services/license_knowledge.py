"""오픈소스 라이선스 의무사항 안내를 위한 최소 RAG 레이어.

지식 문서 범위: license_client.py의 _CAUTION_LICENSE_PREFIXES와 동일한
8개 copyleft 계열 라이선스 문서(knowledge/licenses/*.md). 문서 수가 적어
별도 벡터 DB 없이 메모리에 임베딩을 캐싱하고 코사인 유사도로 검색한다.

deps.dev가 반환하는 license 문자열은 SPDX 표준과 다르게 표기될 때가 있어
(예: "GNU General Public License v3.0" vs "GPL-3.0"), 정확 매칭 대신
임베딩 유사도 검색으로 가장 가까운 문서를 찾는다.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

from google import genai
from google.genai import types

logger = logging.getLogger("ip-sentinel.license_knowledge")

_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "licenses"
_EMBED_MODEL = "text-multilingual-embedding-002"  # 한국어 지원 명시적으로 검증된 모델
_MIN_SIMILARITY = 0.5  # TODO: 실제 임베딩 나온 값 보고 조정 필요

_client = genai.Client()  # .env의 GOOGLE_GENAI_USE_VERTEXAI/PROJECT/LOCATION 자동 사용
_doc_cache: list[dict] | None = None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def _load_and_embed_docs() -> list[dict]:
    global _doc_cache
    if _doc_cache is not None:
        return _doc_cache
    docs = [
        {"license_id": p.stem, "text": p.read_text(encoding="utf-8")}
        for p in sorted(_KNOWLEDGE_DIR.glob("*.md"))
    ]
    if docs:
        result = _client.models.embed_content(
            model=_EMBED_MODEL,
            contents=[d["text"] for d in docs],
            config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
        )
        for doc, embedding in zip(docs, result.embeddings):
            doc["embedding"] = embedding.values
    _doc_cache = docs
    return docs


def retrieve_license_doc(license_str: str, *, top_k: int = 1) -> str:
    """license_str과 의미적으로 가장 가까운 의무사항 문서를 반환한다.
    안전 라이선스(MIT 등)는 코퍼스에 없으므로 '주의' 판정일 때만 호출하면 된다.
    """
    if not license_str:
        return ""
    docs = _load_and_embed_docs()
    if not docs:
        return ""
    try:
        query = _client.models.embed_content(
            model=_EMBED_MODEL,
            contents=[license_str],
            config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
        )
        query_vec = query.embeddings[0].values
    except Exception:
        logger.exception("라이선스 문서 검색용 임베딩 생성 실패")
        return ""

    scored = sorted(
        ((doc, _cosine_similarity(query_vec, doc["embedding"])) for doc in docs),
        key=lambda pair: pair[1], reverse=True,
    )
    top = scored[:top_k]
    if not top or top[0][1] < _MIN_SIMILARITY:
        return ""
    return "\n\n".join(doc["text"] for doc, _score in top)
