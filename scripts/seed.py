"""Seed a demo workspace: org, owner login, a completed sprint, ideas and a portfolio.

Safe to run repeatedly: existing org/user are reused and each run adds a new sprint.
Usage: python scripts/seed.py [--synthetic]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("LLM_PROVIDER", "mock")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from api.db import SessionLocal, init_db
from api.models import Idea, Portfolio, Strategy
from demo_sprint import DEFAULT_EMAIL, DEFAULT_PASSWORD, ensure_account, run_sprint
from sqlalchemy import select

from agents.embeddings import embed

IDEAS = [
    (
        "Trend-filtered sector momentum",
        "12-1 momentum persists only when the sector trades above its 200-day average and volatility is below median.",
        "cross_sectional",
        ["momentum", "trend"],
    ),
    (
        "Volatility-scaled defensive rotation",
        "Scaling equity exposure inversely to realized volatility reduces drawdown without requiring a return forecast.",
        "defensive",
        ["vol_target"],
    ),
    (
        "Credit-spread regime switch",
        "High-yield spread widening predicts equity drawdowns with a short lag; test after costs and publication delay.",
        "regime",
        ["macro", "credit"],
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed a Nullpoint demo workspace.")
    parser.add_argument("--synthetic", action="store_true", help="force synthetic data")
    args = parser.parse_args()

    summary = run_sprint(
        synthetic=args.synthetic,
        symbols=["SPY", "QQQ", "XLK", "XLF", "XLE", "XLV", "TLT", "GLD"],
        start="2018-01-01",
        end="2024-12-31",
        max_strategies=3,
    )

    init_db()
    session = SessionLocal()
    try:
        org, _ = ensure_account(session, "Skeptical Capital", DEFAULT_EMAIL)
        created = 0
        for title, hypothesis, family, tags in IDEAS:
            exists = session.execute(select(Idea).where(Idea.org_id == org.id, Idea.title == title)).scalar_one_or_none()
            if exists:
                continue
            session.add(
                Idea(
                    org_id=org.id,
                    title=title,
                    hypothesis=hypothesis,
                    family=family,
                    tags=tags,
                    embedding=embed(f"{title} {hypothesis}"),
                    fingerprint=f"demo-{title.lower()[:40].replace(' ', '-')}",
                    origin="human",
                    status="new",
                )
            )
            created += 1
        session.commit()

        generated = session.execute(
            select(Strategy).where(Strategy.sprint_id == summary["sprint_id"])
        ).scalars().all()
        session.add(
            Portfolio(
                org_id=org.id,
                name=f"DEMO · {len(generated)} strategies",
                method="equal_weight",
                weights={row.name: round(1 / max(1, len(generated)), 4) for row in generated},
                metrics={
                    "note": "Equal-weight demo basket. Construct a real portfolio from the UI after validation.",
                },
            )
        )
        session.commit()
        print(
            json.dumps(
                {
                    "login": {"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD},
                    "sprint": summary,
                    "ideas_added": created,
                    "note": "Demo data only. Research software, not investment advice.",
                },
                indent=2,
                default=str,
            )
        )
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
