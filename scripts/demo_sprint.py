"""Run one complete research sprint offline and print a machine-readable summary.

Usage:
    python scripts/demo_sprint.py --synthetic
    python scripts/demo_sprint.py --symbols SPY,QQQ,XLK --start 2018-01-01 --end 2024-12-31
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("LLM_PROVIDER", "mock")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.config import settings
from api.db import SessionLocal, init_db
from api.jobs import create_sprint
from api.models import Organization, Strategy, User
from api.security import hash_password
from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.orchestrator import SprintRunner

DEFAULT_EMAIL = "demo@nullpoint.local"
DEFAULT_PASSWORD = "nullpoint-demo"


def ensure_account(session: Session, org_name: str, email: str) -> tuple[Organization, User]:
    org = session.execute(select(Organization)).scalars().first()
    if org is None:
        slug = org_name.lower().replace(" ", "-")[:60]
        org = Organization(name=org_name, slug=slug, monthly_budget_usd=40.0)
        session.add(org)
        session.commit()
        session.refresh(org)
    user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        user = User(
            org_id=org.id,
            email=email,
            hashed_password=hash_password(DEFAULT_PASSWORD),
            role="owner",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
    return org, user


def run_sprint(
    *,
    synthetic: bool,
    symbols: list[str],
    start: str,
    end: str,
    max_strategies: int,
    org_name: str = "Skeptical Capital",
    email: str = DEFAULT_EMAIL,
) -> dict[str, Any]:
    init_db()
    session = SessionLocal()
    try:
        org, user = ensure_account(session, org_name, email)
        sprint = create_sprint(
            session,
            org.id,
            objective="Falsify cross-sectional momentum and mean-reversion ideas on liquid ETFs",
            universe=symbols,
            budget_usd=5.0,
            config={
                "max_strategies": max_strategies,
                "start": start,
                "end": end,
                "source": "demo",
            },
            created_by=user.id,
        )
        result = SprintRunner(
            session, sprint.id, settings=settings, allow_network=not synthetic
        ).run()
        session.refresh(sprint)
        strategies = session.execute(
            select(Strategy).where(Strategy.sprint_id == sprint.id).order_by(Strategy.created_at)
        ).scalars().all()
        return {
            "sprint_id": sprint.id,
            "status": sprint.status,
            "email": email,
            "password": DEFAULT_PASSWORD,
            "universe": sprint.universe,
            "trials": sprint.trials_used,
            "cost_usd": sprint.cost_usd,
            "dataset": (sprint.result or {}).get("dataset"),
            "sealed_holdout": (sprint.result or {}).get("sealed_holdout"),
            "result_status": result.get("status"),
            "strategies": [
                {
                    "id": row.id,
                    "name": row.name,
                    "status": row.status,
                    "sharpe": (row.metrics or {}).get("sharpe"),
                    "tearsheet": (row.metrics or {}).get("tearsheet_path"),
                }
                for row in strategies
            ],
            "note": "Research only. Not investment advice. Synthetic data is labelled as such.",
        }
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Nullpoint research sprint.")
    parser.add_argument("--synthetic", action="store_true", help="force the offline synthetic dataset")
    parser.add_argument("--symbols", default="SPY,QQQ,XLK,XLF,XLE,XLV")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--max-strategies", type=int, default=2)
    parser.add_argument("--org", default="Skeptical Capital")
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    args = parser.parse_args()
    summary = run_sprint(
        synthetic=args.synthetic,
        symbols=[s.strip().upper() for s in args.symbols.split(",") if s.strip()],
        start=args.start,
        end=args.end,
        max_strategies=max(1, min(args.max_strategies, 6)),
        org_name=args.org,
        email=args.email,
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
