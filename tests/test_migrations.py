from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(database: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database}"
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_migrations_upgrade_and_downgrade(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    upgrade = _run(database, "upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
    assert {"organization", "strategy", "sprint", "job_attempt", "holdout_evaluation"} <= tables
    assert "alembic_version" in tables

    check = _run(database, "check")
    assert check.returncode == 0, check.stdout + check.stderr

    downgrade = _run(database, "downgrade", "base")
    assert downgrade.returncode == 0, downgrade.stderr
    with sqlite3.connect(database) as connection:
        remaining = {
            row[0] for row in connection.execute("select name from sqlite_master where type='table'")
        }
    assert not ({"organization", "strategy", "sprint"} & remaining)
