"""Local Parquet data lake with point-in-time manifests and DuckDB querying.

Layout::

    <root>/<provider>/<symbol>/<interval>/bars.parquet
    <root>/<provider>/<symbol>/<interval>/meta.json

Writes are atomic (temp file + rename) and every asset carries a content hash so experiment
ledgers can prove which bytes produced a result.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_SAFE = re.compile(r"[^A-Za-z0-9_.=-]+")


def _slug(value: str) -> str:
    return _SAFE.sub("-", value.strip())[:120]


@dataclass
class LakeAsset:
    provider: str
    symbol: str
    interval: str
    path: str
    rows: int
    content_hash: str
    bytes: int
    start: str
    end: str
    meta: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "symbol": self.symbol,
            "interval": self.interval,
            "path": self.path,
            "rows": self.rows,
            "content_hash": self.content_hash,
            "bytes": self.bytes,
            "start": self.start,
            "end": self.end,
            "meta": self.meta,
        }


class DataLake:
    def __init__(self, root: str | Path = "./.lake", read_only: bool = False) -> None:
        self.root = Path(root)
        self.read_only = read_only
        if not read_only:
            self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ paths
    def _dir(self, provider: str, symbol: str, interval: str) -> Path:
        return self.root / _slug(provider) / _slug(symbol.upper()) / _slug(interval)

    def path_for(self, provider: str, symbol: str, interval: str = "1d") -> Path:
        return self._dir(provider, symbol, interval) / "bars.parquet"

    # ------------------------------------------------------------------ write
    def write_bars(
        self,
        frame: pd.DataFrame,
        provider: str,
        symbol: str,
        interval: str = "1d",
        extra_meta: dict[str, Any] | None = None,
    ) -> LakeAsset:
        if self.read_only:
            raise PermissionError("data lake is read-only (DATA_READ_ONLY=true)")
        if frame.empty:
            raise ValueError("refusing to write an empty frame")
        target_dir = self._dir(provider, symbol, interval)
        target_dir.mkdir(parents=True, exist_ok=True)
        bars_path = target_dir / "bars.parquet"
        tmp_path = target_dir / f".bars.{os.getpid()}.tmp"

        out = frame.copy()
        out.index.name = "date"
        meta = {
            "provider": provider,
            "symbol": symbol.upper(),
            "interval": interval,
            "rows": len(out),
            "start": str(out.index[0].date()),
            "end": str(out.index[-1].date()),
            "fetched_at": pd.Timestamp.utcnow().isoformat(),
            "attrs": {k: v for k, v in out.attrs.items() if isinstance(v, (str, int, float, bool))},
            **(extra_meta or {}),
        }
        with self._lock:
            out.to_parquet(tmp_path, engine="pyarrow", compression="zstd")
            os.replace(tmp_path, bars_path)
            digest = hashlib.sha256(bars_path.read_bytes()).hexdigest()
            meta["content_hash"] = digest
            (target_dir / "meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")

        return LakeAsset(
            provider=provider,
            symbol=symbol.upper(),
            interval=interval,
            path=str(bars_path),
            rows=len(out),
            content_hash=digest,
            bytes=bars_path.stat().st_size,
            start=meta["start"],
            end=meta["end"],
            meta=meta,
        )

    # ------------------------------------------------------------------- read
    def read_bars(
        self, provider: str, symbol: str, interval: str = "1d"
    ) -> tuple[pd.DataFrame, dict[str, Any]] | None:
        path = self.path_for(provider, symbol, interval)
        if not path.exists():
            return None
        frame = pd.read_parquet(path, engine="pyarrow")
        frame.index = pd.DatetimeIndex(frame.index)
        meta_path = path.parent / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        frame.attrs.update(meta.get("attrs", {}))
        return frame, meta

    def manifests(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not self.root.exists():
            return out
        for meta_path in sorted(self.root.glob("*/*/*/meta.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            meta["path"] = str(meta_path.parent / "bars.parquet")
            out.append(meta)
        return out

    def delete(self, provider: str, symbol: str, interval: str = "1d") -> bool:
        if self.read_only:
            raise PermissionError("data lake is read-only")
        path = self._dir(provider, symbol, interval)
        if not path.exists():
            return False
        for child in path.iterdir():
            child.unlink()
        path.rmdir()
        return True

    # --------------------------------------------------------------- duckdb
    def query(self, sql: str, limit: int = 500) -> pd.DataFrame:
        """Run a read-only SQL query against a ``bars`` view over every Parquet asset."""
        import duckdb

        statement = sql.strip().rstrip(";")
        lowered = statement.lower()
        if not (lowered.startswith("select") or lowered.startswith("with") or lowered.startswith("describe")):
            raise ValueError("only SELECT / WITH / DESCRIBE queries are permitted")
        if ";" in statement:
            raise ValueError("multiple statements are not permitted")
        if not self.root.exists():
            return pd.DataFrame()
        con = duckdb.connect(database=":memory:", read_only=False)
        try:
            glob = str(self.root / "*" / "*" / "*" / "bars.parquet")
            con.execute(
                f"CREATE VIEW bars AS SELECT * FROM read_parquet('{glob}', union_by_name=true)"
            )
            result = con.execute(statement).fetchdf()
        finally:
            con.close()
        return result.head(limit)

    # ------------------------------------------------------------- ttl cache
    def cached(
        self,
        provider: str,
        symbol: str,
        interval: str,
        fetcher: Callable[[], pd.DataFrame],
        *,
        ttl_hours: float = 24.0,
        force: bool = False,
    ) -> tuple[pd.DataFrame, dict[str, Any], bool]:
        """Return (frame, meta, cache_hit). Never silently serves stale data past TTL."""
        cached = self.read_bars(provider, symbol, interval)
        if cached and not force:
            frame, meta = cached
            fetched_at = pd.Timestamp(
                str(meta.get("attrs", {}).get("fetched_at") or meta.get("fetched_at") or "")
            )
            if pd.isna(fetched_at):  # pragma: no cover - legacy assets without a timestamp
                fetched_at = pd.Timestamp(str(meta.get("start") or "1970-01-01"))
            if pd.notna(fetched_at):
                age_hours = (pd.Timestamp.utcnow().tz_localize(None) - fetched_at.tz_localize(None)).total_seconds() / 3600
                if age_hours <= ttl_hours:
                    return frame, meta, True
        frame = fetcher()
        asset = self.write_bars(frame, provider, symbol, interval)
        return frame, asset.meta, False

    def content_hash_of(self, frame: pd.DataFrame) -> str:
        digest = hashlib.sha256()
        digest.update(np.ascontiguousarray(frame.to_numpy(dtype="float64")).tobytes())
        return digest.hexdigest()
