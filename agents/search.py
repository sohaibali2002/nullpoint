"""Web research providers: Tavily, Exa and Firecrawl behind one normalised interface.

The agents should not care which search vendor is configured.  Each provider returns a
small, comparable hit: title, URL, snippet, provider and score.  The router queries every
configured provider concurrently, de-duplicates by canonical URL, and degrades honestly
when no key is present — it never invents sources.

All HTTP goes through ``httpx.post`` at module level so tests can inject a transport without
touching the network.  Nothing here is allowed to bypass ``ALLOW_NETWORK``; the ToolContext
enforces that before calling the router.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from engine.performance import parallel_map

TAVILY_URL = "https://api.tavily.com/search"
EXA_URL = "https://api.exa.ai/search"
FIRECRAWL_URL = "https://api.firecrawl.dev/v1/search"


class SearchError(RuntimeError):
    """A provider failed in a way the caller should surface, not silently swallow."""


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    provider: str
    score: float = 0.0
    published: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchResult:
    query: str
    hits: list[SearchHit] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "hits": [hit.as_dict() for hit in self.hits],
            "providers": self.providers,
            "errors": self.errors,
            "count": len(self.hits),
        }


class SearchProvider(Protocol):
    name: str
    api_key: str | None

    def search(self, query: str, max_results: int, timeout: float) -> list[SearchHit]: ...


def _text(value: Any, limit: int = 1200) -> str:
    return " ".join(str(value or "").split())[:limit]


def canonical_url(url: str) -> str:
    """Normalise URLs for de-duplication without changing the destination."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.startswith("utm_")]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


@dataclass
class TavilyProvider:
    api_key: str | None
    name: str = "tavily"

    def search(self, query: str, max_results: int, timeout: float) -> list[SearchHit]:
        if not self.api_key:
            raise SearchError("TAVILY_API_KEY is not set")
        response = httpx.post(
            TAVILY_URL,
            json={
                "api_key": self.api_key,
                "query": query,
                "search_depth": "advanced",
                "max_results": max_results,
                "include_answer": False,
                "include_raw_content": False,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        hits = []
        for item in payload.get("results", [])[:max_results]:
            hits.append(
                SearchHit(
                    title=_text(item.get("title"), 300),
                    url=str(item.get("url") or ""),
                    snippet=_text(item.get("content")),
                    provider=self.name,
                    score=float(item.get("score") or 0.0),
                )
            )
        return [hit for hit in hits if hit.url]


@dataclass
class ExaProvider:
    api_key: str | None
    name: str = "exa"

    def search(self, query: str, max_results: int, timeout: float) -> list[SearchHit]:
        if not self.api_key:
            raise SearchError("EXA_API_KEY is not set")
        response = httpx.post(
            EXA_URL,
            headers={"x-api-key": self.api_key, "content-type": "application/json"},
            json={
                "query": query,
                "numResults": max_results,
                "contents": {"text": {"maxCharacters": 1200}},
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        hits = []
        for item in payload.get("results", [])[:max_results]:
            hits.append(
                SearchHit(
                    title=_text(item.get("title"), 300),
                    url=str(item.get("url") or ""),
                    snippet=_text(item.get("text") or item.get("summary")),
                    provider=self.name,
                    score=float(item.get("score") or 0.0),
                    published=item.get("publishedDate"),
                )
            )
        return [hit for hit in hits if hit.url]


@dataclass
class FirecrawlProvider:
    api_key: str | None
    name: str = "firecrawl"

    def search(self, query: str, max_results: int, timeout: float) -> list[SearchHit]:
        if not self.api_key:
            raise SearchError("FIRECRAWL_API_KEY is not set")
        response = httpx.post(
            FIRECRAWL_URL,
            headers={"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
            json={
                "query": query,
                "limit": max_results,
                "scrapeOptions": {"formats": ["markdown"], "onlyMainContent": True},
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") or payload.get("results") or []
        hits = []
        for item in rows[:max_results]:
            hits.append(
                SearchHit(
                    title=_text(item.get("title"), 300),
                    url=str(item.get("url") or ""),
                    snippet=_text(item.get("markdown") or item.get("description")),
                    provider=self.name,
                    score=float(item.get("score") or 0.0),
                )
            )
        return [hit for hit in hits if hit.url]


PROVIDERS: dict[str, type[TavilyProvider] | type[ExaProvider] | type[FirecrawlProvider]] = {
    "tavily": TavilyProvider,
    "exa": ExaProvider,
    "firecrawl": FirecrawlProvider,
}


def _cache_key(provider: str, query: str, max_results: int) -> str:
    blob = f"{provider}|{query.strip().lower()}|{max_results}".encode()
    return hashlib.sha256(blob).hexdigest()


class SearchRouter:
    """Configured providers, concurrent search, de-duplication and a small TTL cache."""

    def __init__(
        self,
        *,
        keys: dict[str, str | None] | None = None,
        enabled: list[str] | None = None,
        timeout: float = 25.0,
        cache_ttl_seconds: float = 900.0,
        max_results: int = 6,
    ) -> None:
        keys = keys or {}
        requested = enabled or list(PROVIDERS)
        self.providers: list[SearchProvider] = []
        for name in requested:
            cls = PROVIDERS.get(name.lower())
            if cls is None:
                continue
            provider = cls(api_key=keys.get(name.lower()))
            if provider.api_key:  # all three providers require a key
                self.providers.append(provider)
        self.timeout = timeout
        self.cache_ttl_seconds = max(0.0, cache_ttl_seconds)
        self.max_results = max(1, max_results)
        self._cache: dict[str, tuple[float, SearchResult]] = {}
        self._lock = threading.Lock()

    @property
    def configured(self) -> list[str]:
        return [provider.name for provider in self.providers]

    def _cached(self, key: str) -> SearchResult | None:
        if self.cache_ttl_seconds <= 0:
            return None
        with self._lock:
            item = self._cache.get(key)
            if item is None:
                return None
            created, value = item
            if time.monotonic() - created > self.cache_ttl_seconds:
                self._cache.pop(key, None)
                return None
            return value

    def _store(self, key: str, value: SearchResult) -> None:
        if self.cache_ttl_seconds <= 0:
            return
        with self._lock:
            if len(self._cache) > 256:
                oldest = min(self._cache.items(), key=lambda kv: kv[1][0])[0]
                self._cache.pop(oldest, None)
            self._cache[key] = (time.monotonic(), value)

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
        providers: list[str] | None = None,
    ) -> SearchResult:
        query = " ".join(str(query or "").split())
        if not query:
            raise ValueError("search query cannot be empty")
        limit = max(1, min(int(max_results or self.max_results), 20))
        selected = self.providers
        if providers:
            wanted = {p.lower() for p in providers}
            selected = [provider for provider in selected if provider.name in wanted]
        if not selected:
            return SearchResult(
                query=query,
                errors=["no search provider configured; set TAVILY_API_KEY, EXA_API_KEY or FIRECRAWL_API_KEY"],
            )
        key = _cache_key(",".join(provider.name for provider in selected), query, limit)
        cached = self._cached(key)
        if cached is not None:
            return cached

        result = SearchResult(query=query, providers=[p.name for p in selected])

        def run(provider: SearchProvider) -> tuple[str, list[SearchHit] | Exception]:
            try:
                return provider.name, provider.search(query, limit, self.timeout)
            except Exception as exc:  # provider failure must not take down the others
                return provider.name, exc

        outcomes = parallel_map(run, selected, max_workers=min(len(selected), 3))
        merged: dict[str, SearchHit] = {}
        for name, outcome in outcomes:
            if isinstance(outcome, Exception):
                result.errors.append(f"{name}: {type(outcome).__name__}: {outcome}")
                continue
            for hit in outcome:
                url = canonical_url(hit.url)
                if not url:
                    continue
                existing = merged.get(url)
                # Keep the richer snippet; score is only a provider hint, not a ranking truth.
                if existing is None or len(hit.snippet) > len(existing.snippet):
                    merged[url] = hit
        result.hits = sorted(
            merged.values(), key=lambda hit: (hit.score, len(hit.snippet)), reverse=True
        )[:limit]
        self._store(key, result)
        return result


def expand_queries(query: str, max_queries: int = 4) -> list[str]:
    """Turn one research question into complementary evidence-seeking queries.

    This is deliberately deterministic.  An LLM can add flavour, but the router's job is
    to cover mechanism, evidence, counter-evidence and implementation risk even offline.
    """
    base = " ".join(query.split())
    variants = [
        base,
        f"{base} academic research evidence",
        f"{base} market evidence historical performance",
        f"{base} risks failure modes implementation",
    ]
    seen: set[str] = set()
    out: list[str] = []
    for variant in variants:
        if variant.lower() not in seen:
            seen.add(variant.lower())
            out.append(variant)
        if len(out) >= max_queries:
            break
    return out


def deep_research(query: str, *, router: SearchRouter, max_queries: int = 3) -> SearchResult:
    """Run several complementary searches and merge the evidence pool."""
    queries = expand_queries(query, max_queries=max_queries)
    merged = SearchResult(query=query)
    seen: set[str] = set()
    for variant in queries:
        result = router.search(variant)
        merged.providers = sorted(set(merged.providers) | set(result.providers))
        merged.errors.extend(result.errors)
        for hit in result.hits:
            url = canonical_url(hit.url)
            if url and url not in seen:
                seen.add(url)
                merged.hits.append(hit)
    merged.hits.sort(key=lambda hit: (hit.score, len(hit.snippet)), reverse=True)
    return merged


__all__ = [
    "ExaProvider",
    "FirecrawlProvider",
    "SearchError",
    "SearchHit",
    "SearchResult",
    "SearchRouter",
    "TavilyProvider",
    "canonical_url",
    "deep_research",
    "expand_queries",
]
