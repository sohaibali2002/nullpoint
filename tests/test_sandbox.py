from __future__ import annotations

import pytest

from sandbox.runner import LocalSubprocessSandbox, SandboxLimits, run_in_sandbox
from sandbox.scanner import ScanError, scan_code


def test_scanner_allows_numeric_strategy_code():
    code = """
import numpy as np
import pandas as pd

def signal(features, params):
    x = features["momentum"].fillna(0.0)
    return (x > 0).astype(float)
"""
    report = scan_code(code)
    assert set(report.imports) == {"numpy", "pandas"}
    assert "signal" in report.functions


@pytest.mark.parametrize(
    "code",
    [
        "import os\ndef signal(f, p):\n    return 0\n",
        "import subprocess\ndef signal(f, p):\n    return 0\n",
        "import socket\ndef signal(f, p):\n    return 0\n",
        "def signal(f, p):\n    return eval('1+1')\n",
        "def signal(f, p):\n    return open('/etc/passwd').read()\n",
        "def signal(f, p):\n    return ().__class__.__bases__\n",
        "def signal(f, p):\n    with open('x') as fh:\n        return fh\n",
        "def signal(f, p):\n    import importlib\n    return importlib\n",
        "def signal(f, p):\n    return __import__('os')\n",
        "def signal(f, p):\n    global x\n    x = 1\n    return x\n",
        "def signal(f, p):\n    return [].__class__.__mro__\n",
        "async def signal(f, p):\n    return 0\n",
        "def signal(f, p):\n    return b'raw bytes'\n",
        "def signal(f, p)",
    ],
)
def test_scanner_blocks_dangerous_constructs(code: str) -> None:
    with pytest.raises(ScanError):
        scan_code(code)


def test_runner_executes_and_returns_structured_output():
    code = """
import numpy as np

def signal(features, params):
    return float(np.mean([1, 2, 3]))
"""
    result = run_in_sandbox(
        code,
        harness="emit({'signal': signal({}, {}), 'mean': float(np.mean([1, 2, 3]))})",
        payload={"unused": 1},
        backend="local",
    )
    assert result.ok, result.stderr
    assert result.output is not None
    assert result.output["signal"] == 2.0
    assert result.output["mean"] == 2.0
    assert result.scan["imports"] == ["numpy"]


def test_runner_receives_payload():
    result = run_in_sandbox(
        "value = PAYLOAD['x'] * 2",
        harness="emit({'value': value})",
        payload={"x": 21},
        backend="local",
    )
    assert result.ok
    assert result.output == {"value": 42}


def test_runner_blocks_unsafe_code_before_execution():
    result = run_in_sandbox("import os\nvalue = os.getcwd()", backend="local")
    assert result.ok is False
    assert "static scan rejected" in result.stderr
    assert result.scan.get("blocked")


def test_runner_times_out_infinite_loop():
    sandbox = LocalSubprocessSandbox(limits=SandboxLimits(timeout_seconds=1, cpu_seconds=2))
    result = sandbox.run("x = 0\nwhile True:\n    x += 1")
    assert result.ok is False
    assert result.timed_out or result.exit_code not in (0, None)


def test_runner_reports_python_errors():
    result = run_in_sandbox("raise ValueError('boom')", backend="local")
    assert result.ok is False
    assert "boom" in result.stderr


def test_runner_enforces_output_limit():
    result = run_in_sandbox(
        "value = 'x' * 1000",
        harness="emit({'value': value})",
        backend="local",
        limits=SandboxLimits(max_output_bytes=100),
    )
    assert result.ok
    assert len(result.stdout) <= 200


def test_docker_backend_clearly_reports_missing_daemon():
    from sandbox.runner import DockerSandbox, SandboxError

    sandbox = DockerSandbox()
    if not sandbox.available():
        with pytest.raises(SandboxError, match="docker"):
            sandbox.run("value = 1")
