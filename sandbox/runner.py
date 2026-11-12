"""Execute untrusted strategy code outside the API process.

Two backends:

* ``docker`` — production: no network, read-only root FS, read-only data mount, capped CPU/RAM/PIDs,
  non-root user, dropped capabilities, wall-clock kill.
* ``local`` — development/tests only: a fresh process group with rlimits and a stripped environment.
  It is not a security boundary against a determined attacker; CI and production use docker.

Agent code never runs in the API or worker process. ``run_in_sandbox`` is called from the worker
with scanned code, and the result is data (JSON), not objects.
"""

from __future__ import annotations

import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from sandbox.scanner import ScanError, scan_code

MAX_OUTPUT_BYTES = 200_000


class SandboxError(RuntimeError):
    pass


@dataclass
class SandboxLimits:
    cpu_seconds: int = 15
    memory_mb: int = 768
    timeout_seconds: int = 30
    max_output_bytes: int = MAX_OUTPUT_BYTES
    cpus: float = 1.0


@dataclass
class ExecutionResult:
    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    output: dict[str, Any] | None = None
    timed_out: bool = False
    backend: str = "local"
    scan: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "output": self.output,
            "timed_out": self.timed_out,
            "backend": self.backend,
            "scan": self.scan,
        }


_MAIN_TEMPLATE = """\
import json, math, sys

def emit(obj):
    with open("output.json", "w", encoding="utf-8") as fh:
        json.dump(obj, fh, default=str)

PAYLOAD = json.loads({payload!r})

{user_code}

{harness}
"""


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... truncated {len(text) - limit} bytes"


@dataclass
class LocalSubprocessSandbox:
    limits: SandboxLimits = field(default_factory=SandboxLimits)
    backend: str = "local"

    def run(
        self,
        code: str,
        *,
        harness: str = "",
        payload: dict[str, Any] | None = None,
        data_dir: Path | None = None,
    ) -> ExecutionResult:
        try:
            report = scan_code(code)
        except ScanError as exc:
            return ExecutionResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr=f"static scan rejected code: {exc}",
                duration_ms=0,
                backend=self.backend,
                scan={"blocked": exc.findings},
            )

        work = Path(tempfile.mkdtemp(prefix="np-sandbox-"))
        try:
            main = _MAIN_TEMPLATE.format(
                payload=json.dumps(payload or {}, default=str),
                user_code=textwrap.dedent(code),
                harness=textwrap.dedent(harness),
            )
            (work / "main.py").write_text(main, encoding="utf-8")
            started = time.perf_counter()
            cmd = [sys.executable, "-I", "main.py"]
            env = {
                "PATH": "/usr/bin:/bin",
                "PYTHONHASHSEED": "0",
                "PYTHONDONTWRITEBYTECODE": "1",
                "MPLBACKEND": "Agg",
                "HOME": str(work),
                # Numerical libraries must not spawn a thread pool: the process limit is tight and
                # oversubscription stalls otherwise deterministic backtests.
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "NUMEXPR_NUM_THREADS": "1",
            }
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=work,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.limits.timeout_seconds,
                    start_new_session=True,
                    preexec_fn=self._limit,
                    check=False,
                )
                timed_out = False
                exit_code = proc.returncode
                stdout, stderr = proc.stdout, proc.stderr
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                exit_code = None
                raw_out = exc.stdout or ""
                raw_err = exc.stderr or ""
                stdout = raw_out.decode("utf-8", "replace") if isinstance(raw_out, bytes) else raw_out
                decoded_err = raw_err.decode("utf-8", "replace") if isinstance(raw_err, bytes) else raw_err
                stderr = decoded_err + "\nwall-clock limit exceeded"
            duration_ms = int((time.perf_counter() - started) * 1000)
            output = None
            out_file = work / "output.json"
            if out_file.exists() and out_file.stat().st_size <= self.limits.max_output_bytes * 10:
                try:
                    output = json.loads(out_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    stderr += f"\ninvalid output.json: {exc}"
            return ExecutionResult(
                ok=(exit_code == 0 and not timed_out),
                exit_code=exit_code,
                stdout=_truncate(stdout, self.limits.max_output_bytes),
                stderr=_truncate(stderr, self.limits.max_output_bytes),
                duration_ms=duration_ms,
                output=output,
                timed_out=timed_out,
                backend=self.backend,
                scan=report.as_dict(),
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _limit(self) -> None:
        cpu = self.limits.cpu_seconds
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
        mem = self.limits.memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


@dataclass
class DockerSandbox:
    limits: SandboxLimits = field(default_factory=SandboxLimits)
    image: str = "nullpoint-sandbox:latest"
    backend: str = "docker"

    def available(self) -> bool:
        return shutil.which("docker") is not None

    def run(
        self,
        code: str,
        *,
        harness: str = "",
        payload: dict[str, Any] | None = None,
        data_dir: Path | None = None,
    ) -> ExecutionResult:
        try:
            report = scan_code(code)
        except ScanError as exc:
            return ExecutionResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr=f"static scan rejected code: {exc}",
                duration_ms=0,
                backend=self.backend,
                scan={"blocked": exc.findings},
            )
        if not self.available():
            raise SandboxError("docker backend requested but the docker CLI is not available")

        work = Path(tempfile.mkdtemp(prefix="np-sandbox-"))
        work.chmod(0o777)
        try:
            main = _MAIN_TEMPLATE.format(
                payload=json.dumps(payload or {}, default=str),
                user_code=textwrap.dedent(code),
                harness=textwrap.dedent(harness),
            )
            (work / "main.py").write_text(main, encoding="utf-8")
            started = time.perf_counter()
            cmd = [
                "docker", "run", "--rm",
                "--network", "none",
                "--cpus", str(self.limits.cpus),
                "--memory", f"{self.limits.memory_mb}m",
                "--pids-limit", "64",
                "--read-only",
                "--tmpfs", "/tmp:rw,size=64m,noexec",
                "--user", "65534:65534",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "-v", f"{work}:/work:rw",
                "-w", "/work",
            ]  # fmt: skip
            if data_dir is not None:
                cmd += ["-v", f"{Path(data_dir).resolve()}:/data:ro"]
            for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
                cmd += ["-e", f"{key}=1"]
            cmd += [self.image, "python", "-I", "/work/main.py"]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.limits.timeout_seconds + 15,
                    check=False,
                )
                timed_out = False
                exit_code = proc.returncode
                stdout, stderr = proc.stdout, proc.stderr
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                exit_code = None
                raw_out = exc.stdout or ""
                raw_err = exc.stderr or ""
                stdout = raw_out.decode("utf-8", "replace") if isinstance(raw_out, bytes) else raw_out
                decoded_err = raw_err.decode("utf-8", "replace") if isinstance(raw_err, bytes) else raw_err
                stderr = decoded_err + "\ncontainer wall-clock limit exceeded"
            duration_ms = int((time.perf_counter() - started) * 1000)
            output = None
            out_file = work / "output.json"
            if out_file.exists():
                try:
                    output = json.loads(out_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    stderr += f"\ninvalid output.json: {exc}"
            return ExecutionResult(
                ok=exit_code == 0 and not timed_out,
                exit_code=exit_code,
                stdout=_truncate(stdout, self.limits.max_output_bytes),
                stderr=_truncate(stderr, self.limits.max_output_bytes),
                duration_ms=duration_ms,
                output=output,
                timed_out=timed_out,
                backend=self.backend,
                scan=report.as_dict(),
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)


def run_in_sandbox(
    code: str,
    *,
    harness: str = "",
    payload: dict[str, Any] | None = None,
    data_dir: Path | None = None,
    backend: Literal["auto", "docker", "local"] = "auto",
    limits: SandboxLimits | None = None,
) -> ExecutionResult:
    limits = limits or SandboxLimits()
    if backend == "auto":
        backend = "docker" if os.environ.get("NULLPOINT_SANDBOX_BACKEND", "local") == "docker" else "local"
    runner = DockerSandbox(limits=limits) if backend == "docker" else LocalSubprocessSandbox(limits=limits)
    return runner.run(code, harness=harness, payload=payload, data_dir=data_dir)


def kill_process_group(pid: int) -> None:  # pragma: no cover - used by the worker kill switch
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except ProcessLookupError:
        return
