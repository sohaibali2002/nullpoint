"""Ingest a public summary or a licensed text into the shared Nullpoint library.

This is deliberately an operator tool, not a public endpoint. It refuses to scrape a book
repository wholesale; pass the URL of a summary, a public-domain text, or a file you own.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
from api.config import settings
from api.db import SessionLocal
from api.models import Organization

from agents.knowledge import LIBRARY_ORG_ID, KnowledgeBase


def _read(source: str) -> tuple[str, str]:
    if source.startswith("http://") or source.startswith("https://"):
        response = httpx.get(source, timeout=60, follow_redirects=True)
        response.raise_for_status()
        return source.rsplit("/", 1)[-1] or "library document", response.text
    path = Path(source)
    return path.name, path.read_text(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest a document into the shared library")
    parser.add_argument("source", help="URL or local UTF-8 text file")
    parser.add_argument("--title", default="")
    parser.add_argument("--url", default="")
    parser.add_argument("--tags", default="library,investing")
    parser.add_argument("--max-chars", type=int, default=400_000)
    args = parser.parse_args()

    filename, body = _read(args.source)
    if not body.strip():
        print("source was empty", file=sys.stderr)
        return 1
    body = body[: args.max_chars]
    title = (args.title or filename).strip()[:200]

    session = SessionLocal()
    try:
        if session.get(Organization, LIBRARY_ORG_ID) is None:
            session.add(
                Organization(
                    id=LIBRARY_ORG_ID,
                    name="Nullpoint Shared Library",
                    slug="nullpoint-shared-library",
                    plan="library",
                    monthly_budget_usd=0,
                )
            )
            session.commit()
        result = KnowledgeBase(
            session,
            LIBRARY_ORG_ID,
            api_key=settings.voyage_key,
            model=settings.voyage_model,
            base_url=settings.voyage_base_url,
            chunk_chars=settings.knowledge_chunk_chars,
        ).add(
            title,
            body,
            source="library",
            url=args.url or (args.source if args.source.startswith("http") else ""),
            tags=[tag.strip() for tag in args.tags.split(",") if tag.strip()],
            metadata={"public_library": True},
        )
        print(
            f"ingested title={result['title']!r} chunks={result['chunks']} "
            f"model={result['model']} fallback={result['fallback']}"
        )
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
