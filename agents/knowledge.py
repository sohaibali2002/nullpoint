"""Voyage-backed knowledge base with an offline fallback.

Voyage AI supplies high-quality document/query embeddings.  The knowledge base stores each
chunk in the existing ``memory`` table (kind ``knowledge``) so there is no schema migration
and every hit stays inside the tenant boundary.  When ``VOYAGE_API_KEY`` is absent — or the
Voyage request fails — the deterministic local embedding is used instead.  Stored vectors
record which model produced them, and search only compares vectors of the same dimension,
so mixing an offline corpus with a Voyage corpus cannot produce nonsense similarities.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
import numpy as np
from api.models import Memory, new_id
from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.embeddings import DIMENSIONS as LOCAL_DIMENSIONS
from agents.embeddings import cosine
from agents.embeddings import embed as local_embed

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
DEFAULT_MODEL = "voyage-3-large"
# The shared, read-only desk library.  Tenant searches include it; tenant writes never do.
LIBRARY_ORG_ID = "nullpoint-library"
DEFAULT_CHUNK_CHARS = 1400
DEFAULT_OVERLAP_CHARS = 160
# Voyage's free tier is limited to a few requests per minute.  These batches stay comfortably
# below its token-per-minute ceiling and are paced instead of failing a whole ingestion.
VOYAGE_BATCH_CHARS = 9_000
VOYAGE_MAX_ATTEMPTS = 4
VOYAGE_PACE_SECONDS = 21.0
# A single serverless upload must finish inside the platform timeout.  Larger books still index
# immediately with the deterministic local model and remain searchable alongside Voyage chunks.
VOYAGE_MAX_DOCUMENT_CHARS = 45_000
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    model: str
    dimensions: int
    fallback: bool = False


class VoyageEmbedder:
    """Small REST client for Voyage's embeddings endpoint (no SDK dependency)."""

    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = VOYAGE_URL,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _batches(texts: list[str], max_chars: int = VOYAGE_BATCH_CHARS) -> list[list[str]]:
        batches: list[list[str]] = []
        current: list[str] = []
        size = 0
        for text in texts:
            length = len(text)
            if current and size + length > max_chars:
                batches.append(current)
                current = []
                size = 0
            current.append(text)
            size += length
        if current:
            batches.append(current)
        return batches

    def _request(self, texts: list[str], input_type: str) -> EmbeddingBatch:
        response = httpx.post(
            self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
            json={"model": self.model, "input": texts, "input_type": input_type},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") or []
        vectors = [row.get("embedding") for row in rows]
        if len(vectors) != len(texts) or any(not isinstance(vector, list) or not vector for vector in vectors):
            raise RuntimeError("Voyage returned a malformed embedding response")
        dimensions = len(vectors[0])
        if any(len(vector) != dimensions for vector in vectors):
            raise RuntimeError("Voyage returned inconsistent embedding dimensions")
        return EmbeddingBatch(vectors, self.model, dimensions)

    def embed(self, texts: list[str], *, input_type: str = "document") -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch([], f"{self.model}:empty", 0)
        if not self.api_key:
            raise RuntimeError("VOYAGE_API_KEY is not set")
        vectors: list[list[float]] = []
        dimensions = 0
        for index, batch in enumerate(self._batches(texts)):
            if index:
                # Pace document ingestion to the account's request/token ceiling.
                time.sleep(VOYAGE_PACE_SECONDS)
            for attempt in range(VOYAGE_MAX_ATTEMPTS):
                try:
                    result = self._request(batch, input_type)
                    break
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 429 or attempt == VOYAGE_MAX_ATTEMPTS - 1:
                        raise
                    time.sleep(VOYAGE_PACE_SECONDS * (attempt + 1))
            dimensions = result.dimensions
            vectors.extend(result.vectors)
        return EmbeddingBatch(vectors, self.model, dimensions)


def embed_batch(
    texts: list[str],
    *,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    base_url: str = VOYAGE_URL,
    input_type: str = "document",
    timeout: float = 30.0,
    max_voyage_chars: int | None = None,
) -> EmbeddingBatch:
    """Use Voyage when configured; otherwise fall back to the deterministic local model."""
    if max_voyage_chars is not None and sum(len(text) for text in texts) > max_voyage_chars:
        api_key = None
    if api_key:
        try:
            return VoyageEmbedder(api_key, model=model, base_url=base_url, timeout=timeout).embed(
                texts, input_type=input_type
            )
        except Exception:
            # A vendor outage should degrade research, not halt the sprint.  The model field
            # below makes the fallback visible in every stored chunk.
            pass
    vectors = [local_embed(text) for text in texts]
    return EmbeddingBatch(vectors, "local-hash-256", LOCAL_DIMENSIONS, fallback=True)


def chunk_text(
    text: str,
    *,
    max_chars: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    """Split at paragraph/sentence boundaries with a small overlap for retrieval context."""
    normalized = _WHITESPACE.sub(" ", str(text or "")).strip()
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + max_chars)
        if end < len(normalized):
            boundary = max(normalized.rfind(". ", start, end), normalized.rfind("; ", start, end))
            if boundary > start + max_chars // 2:
                end = boundary + 1
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(start + 1, end - max(0, overlap))
    return chunks


def _snippet(body: str, query: str, limit: int = 320) -> str:
    if len(body) <= limit:
        return body
    words = [word for word in re.findall(r"[a-z0-9]+", query.lower()) if len(word) > 2]
    lowered = body.lower()
    positions = [lowered.find(word) for word in words]
    positions = [pos for pos in positions if pos >= 0]
    if positions:
        start = max(0, min(positions) - 80)
        return ("…" if start else "") + body[start : start + limit].strip() + "…"
    return body[:limit].strip() + "…"


class KnowledgeBase:
    """Tenant-scoped document chunks with semantic search."""

    def __init__(
        self,
        session: Session,
        org_id: str,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str = VOYAGE_URL,
        chunk_chars: int = DEFAULT_CHUNK_CHARS,
        overlap_chars: int = DEFAULT_OVERLAP_CHARS,
        timeout: float = 30.0,
        include_library: bool = True,
    ) -> None:
        self.session = session
        self.org_id = org_id
        self.include_library = include_library and org_id != LIBRARY_ORG_ID
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.chunk_chars = max(200, int(chunk_chars))
        self.overlap_chars = max(0, min(int(overlap_chars), self.chunk_chars // 2))
        self.timeout = timeout

    def add(
        self,
        title: str,
        body: str,
        *,
        source: str = "manual",
        url: str = "",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        title = _WHITESPACE.sub(" ", str(title or "")).strip()[:200]
        chunks = chunk_text(body, max_chars=self.chunk_chars, overlap=self.overlap_chars)
        if not title:
            raise ValueError("knowledge title cannot be empty")
        if not chunks:
            raise ValueError("knowledge body cannot be empty")
        batch = embed_batch(
            chunks,
            api_key=self.api_key,
            model=self.model,
            base_url=self.base_url,
            input_type="document",
            timeout=self.timeout,
            max_voyage_chars=VOYAGE_MAX_DOCUMENT_CHARS,
        )
        tag_list = sorted({str(tag).strip().lower() for tag in (tags or []) if str(tag).strip()})
        source_host = urlsplit(url).netloc if url else source
        ids: list[str] = []
        for index, (chunk, vector) in enumerate(zip(chunks, batch.vectors, strict=True)):
            content_hash = hashlib.sha256(f"{title}|{source}|{chunk}".encode()).hexdigest()
            row = Memory(
                id=new_id(),
                org_id=self.org_id,
                kind="knowledge",
                title=title,
                body=chunk,
                embedding=vector,
                payload={
                    "source": source,
                    "url": url,
                    "source_host": source_host,
                    "tags": tag_list,
                    "chunk_index": index,
                    "chunk_count": len(chunks),
                    "content_hash": content_hash,
                    "embedding_model": batch.model,
                    "embedding_dimensions": batch.dimensions,
                    "embedding_fallback": batch.fallback,
                    **(metadata or {}),
                },
            )
            self.session.add(row)
            ids.append(row.id)
        self.session.commit()
        return {
            "created": len(ids),
            "memory_ids": ids,
            "title": title,
            "model": batch.model,
            "fallback": batch.fallback,
            "chunks": len(chunks),
        }

    def search(
        self,
        query: str,
        *,
        limit: int = 6,
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        query = _WHITESPACE.sub(" ", str(query or "")).strip()
        if not query:
            return []
        batch = embed_batch(
            [query],
            api_key=self.api_key,
            model=self.model,
            base_url=self.base_url,
            input_type="query",
            timeout=self.timeout,
        )
        query_vectors = [np.asarray(batch.vectors[0], dtype=float)]
        if batch.model != "local-hash-256":
            # Uploads can legitimately fall back to the local model (large files or a vendor
            # limit). Compare those rows with a local query vector instead of hiding them.
            local = embed_batch([query], api_key=None)
            query_vectors.append(np.asarray(local.vectors[0], dtype=float))
        wanted = {str(tag).strip().lower() for tag in (tags or []) if str(tag).strip()}
        org_ids = [self.org_id]
        if self.include_library:
            org_ids.append(LIBRARY_ORG_ID)
        rows = (
            self.session.execute(
                select(Memory)
                .where(Memory.org_id.in_(org_ids), Memory.kind == "knowledge")
                .order_by(Memory.created_at.desc())
                .limit(2000)
            )
            .scalars()
            .all()
        )
        scored: list[tuple[float, Memory]] = []
        for row in rows:
            if not row.embedding:
                continue
            vector = next(
                (candidate for candidate in query_vectors if len(candidate) == len(row.embedding)),
                None,
            )
            if vector is None:
                continue
            row_tags = {str(tag).lower() for tag in (row.payload or {}).get("tags", [])}
            if wanted and not wanted.issubset(row_tags):
                continue
            score = cosine(vector, row.embedding)
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        out = []
        for score, row in scored[: max(1, min(int(limit), 20))]:
            payload = row.payload or {}
            out.append(
                {
                    "id": row.id,
                    "title": row.title,
                    "snippet": _snippet(row.body, query),
                    "score": round(float(score), 4),
                    "source": payload.get("source", ""),
                    "url": payload.get("url", ""),
                    "tags": payload.get("tags", []),
                    "chunk_index": payload.get("chunk_index", 0),
                    "embedding_model": payload.get("embedding_model", ""),
                }
            )
        return out

    def stats(self) -> dict[str, Any]:
        org_ids = [self.org_id]
        if self.include_library:
            org_ids.append(LIBRARY_ORG_ID)
        rows = (
            self.session.execute(
                select(Memory).where(Memory.org_id.in_(org_ids), Memory.kind == "knowledge")
            )
            .scalars()
            .all()
        )
        models = sorted({str((row.payload or {}).get("embedding_model", "")) for row in rows})
        documents = {
            (row.title, str((row.payload or {}).get("url", "")), str((row.payload or {}).get("source", "")))
            for row in rows
        }
        library_chunks = sum(1 for row in rows if row.org_id == LIBRARY_ORG_ID)
        return {
            "documents": len(documents),
            "chunks": len(rows),
            "library_chunks": library_chunks,
            "embedding_models": [model for model in models if model],
            "voyage_configured": bool(self.api_key),
        }


__all__ = [
    "DEFAULT_MODEL",
    "LIBRARY_ORG_ID",
    "EmbeddingBatch",
    "KnowledgeBase",
    "VoyageEmbedder",
    "chunk_text",
    "embed_batch",
]
