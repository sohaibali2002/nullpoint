"""Compile agent-written signal code only after a static security scan.

This module is the *second* line of defence. The first is the container sandbox. The API process
never executes untrusted code with network/filesystem access: ``compile_signal`` is called inside
the sandboxed worker for agent-authored strategies, and directly only for bundled/trusted code.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd

from sandbox.scanner import ScanError, scan_code

SAFE_BUILTINS: dict[str, Any] = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
    "None": None,
    "True": True,
    "False": False,
}

SignalFn = Callable[..., Any]


class SignalError(RuntimeError):
    """Raised when a signal cannot be compiled, runs out of time, or returns an unusable frame."""


def compile_signal(code: str) -> SignalFn:
    if not code.strip():
        raise SignalError("signal code is empty")
    scan_code(code)  # raises ScanError
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:  # pragma: no cover - scanner parses first
        raise SignalError(f"signal code does not parse: {exc}") from exc
    if not any(isinstance(node, ast.FunctionDef) and node.name == "signal" for node in tree.body):
        raise SignalError("signal code must define a top-level function named 'signal'")

    namespace: dict[str, Any] = {
        "__builtins__": SAFE_BUILTINS,
        "np": np,
        "pd": pd,
    }
    try:
        exec(compile(tree, "<signal>", "exec"), namespace)
    except ScanError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise SignalError(f"failed to load signal: {exc}") from exc

    fn = namespace.get("signal")
    if not callable(fn):
        raise SignalError("'signal' is not callable")
    return fn


def evaluate_signal(
    fn: SignalFn,
    features: dict[str, pd.DataFrame],
    close: pd.DataFrame,
    params: dict[str, Any],
) -> pd.DataFrame:
    """Run the signal and coerce its output to a valid weight matrix."""
    try:
        raw = fn(features=features, params=params, close=close)
    except TypeError:
        try:
            raw = fn(features, params)
        except Exception as exc:
            raise SignalError(f"signal raised: {exc}") from exc
    except Exception as exc:
        raise SignalError(f"signal raised: {exc}") from exc

    if isinstance(raw, pd.Series):
        raw = raw.to_frame(name=close.columns[0])
    if not isinstance(raw, pd.DataFrame):
        raise SignalError(f"signal must return a DataFrame, got {type(raw).__name__}")

    frame = raw.reindex(index=close.index, columns=close.columns)
    if frame.isna().all(axis=None):
        raise SignalError("signal returned no usable values")
    frame = frame.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)
    if frame.abs().to_numpy().max() > 10:
        raise SignalError("signal weights exceed +/-10; size positions in risk rules instead")
    return frame
