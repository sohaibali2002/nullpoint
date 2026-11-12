"""Static AST allowlist for strategy code.

Blocks filesystem, network, process, introspection and dynamic-execution primitives before any
code runs. This is defence in depth: the runner also enforces no network, read-only mounts and
CPU/memory/time limits, but a strategy never needs these primitives, so rejecting them outright
keeps the attack surface small.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        "math",
        "statistics",
        "numpy",
        "pandas",
        "scipy",
        "itertools",
        "functools",
        "collections",
        "decimal",
        "fractions",
        "typing",
        "dataclasses",
        "sklearn",
        "statsmodels",
    }
)

BLOCKED_MODULES: frozenset[str] = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "socket",
        "socketserver",
        "shutil",
        "pathlib",
        "glob",
        "tempfile",
        "importlib",
        "imp",
        "builtins",
        "__builtin__",
        "ctypes",
        "cffi",
        "multiprocessing",
        "threading",
        "concurrent",
        "asyncio",
        "signal",
        "resource",
        "pty",
        "fcntl",
        "termios",
        "pickle",
        "marshal",
        "shelve",
        "dbm",
        "sqlite3",
        "requests",
        "httpx",
        "urllib",
        "urllib2",
        "urllib3",
        "http",
        "ftplib",
        "smtplib",
        "telnetlib",
        "paramiko",
        "docker",
        "boto3",
        "botocore",
        "google",
        "azure",
        "secrets",
        "random",  # determinism: signals must be a pure function of features
        "time",
        "datetime",
        "logging",
        "warnings",
        "inspect",
        "gc",
        "code",
        "codeop",
        "compileall",
        "runpy",
        "site",
        "traceback",
        "webbrowser",
        "platform",
        "getpass",
        "pwd",
        "grp",
        "crypt",
        "ssl",
        "hashlib",
        "hmac",
        "uuid",
    }
)

BLOCKED_NAMES: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "__import__",
        "globals",
        "locals",
        "vars",
        "dir",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "input",
        "breakpoint",
        "memoryview",
        "help",
        "exit",
        "quit",
        "super",
        "object",
        "type",
        "classmethod",
        "staticmethod",
        "property",
        "print",
    }
)

BLOCKED_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "__subclasses__",
        "__globals__",
        "__code__",
        "__closure__",
        "__class__",
        "__bases__",
        "__mro__",
        "__dict__",
        "__getattribute__",
        "__reduce__",
        "__reduce_ex__",
        "__init_subclass__",
        "__set_name__",
        "__loader__",
        "__spec__",
        "__builtins__",
        "__import__",
        "system",
        "popen",
        "fork",
        "execv",
        "execve",
        "spawn",
        "spawnl",
        "remove",
        "unlink",
        "rmtree",
        "chmod",
        "chown",
        "read_text",
        "write_text",
        "read_bytes",
        "write_bytes",
        "to_csv",
        "to_pickle",
        "to_parquet",
        "read_pickle",
        "load",
        "dump",
        "loads",
        "dumps",
    }
)

SUSPICIOUS_ATTRIBUTES: frozenset[str] = frozenset(
    {"read", "write", "connect", "send", "recv", "env", "environ", "getenv", "now", "today"}
)


class ScanError(ValueError):
    def __init__(self, findings: list[str]) -> None:
        self.findings = findings
        super().__init__("; ".join(findings))


@dataclass
class ScanReport:
    imports: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    max_loop_depth: int = 0
    lines: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "imports": self.imports,
            "functions": self.functions,
            "warnings": self.warnings,
            "max_loop_depth": self.max_loop_depth,
            "lines": self.lines,
        }


def _module_allowed(module: str) -> bool:
    root = module.split(".")[0]
    if root in BLOCKED_MODULES:
        return False
    return root in ALLOWED_MODULES


class _Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.findings: list[str] = []
        self.report = ScanReport()
        self.loop_depth = 0

    def _fail(self, node: ast.AST, message: str) -> None:
        self.findings.append(f"line {getattr(node, 'lineno', '?')}: {message}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.report.imports.append(alias.name)
            if not _module_allowed(alias.name):
                self._fail(node, f"import of blocked module {alias.name!r}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        self.report.imports.append(module)
        if not _module_allowed(module):
            self._fail(node, f"from-import of blocked module {module!r}")
        for alias in node.names:
            if alias.name.startswith("_") and alias.name not in {"__future__"}:
                self._fail(node, f"private import {alias.name!r}")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.report.functions.append(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._fail(node, "async code is not permitted in strategy signals")
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in BLOCKED_NAMES:
            self._fail(node, f"use of blocked name {node.id!r}")
        if node.id.startswith("__") and node.id not in {"__name__"}:
            self._fail(node, f"dunder name {node.id!r}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in BLOCKED_ATTRIBUTES:
            self._fail(node, f"access to blocked attribute {node.attr!r}")
        elif node.attr in SUSPICIOUS_ATTRIBUTES:
            self.report.warnings.append(
                f"line {node.lineno}: attribute {node.attr!r} needs review (not blocked)"
            )
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        self._fail(node, "context managers (with) are not permitted")
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        self.report.warnings.append(f"line {node.lineno}: try/except swallows errors; review")
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.loop_depth += 1
        self.report.max_loop_depth = max(self.report.max_loop_depth, self.loop_depth)
        self.generic_visit(node)
        self.loop_depth -= 1

    def visit_For(self, node: ast.For) -> None:
        self.loop_depth += 1
        self.report.max_loop_depth = max(self.report.max_loop_depth, self.loop_depth)
        self.generic_visit(node)
        self.loop_depth -= 1

    def visit_Global(self, node: ast.Global) -> None:
        self._fail(node, "global statements are not permitted")

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self._fail(node, "nonlocal statements are not permitted")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._fail(node, "class definitions are not permitted in signal code")

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, (bytes, bytearray)):
            self._fail(node, "byte literals are not permitted")


def scan_code(code: str) -> ScanReport:
    """Parse and scan. Raises ScanError listing every finding (not just the first)."""
    if len(code) > 120_000:
        raise ScanError(["code exceeds 120k characters"])
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise ScanError([f"syntax error: {exc.msg} (line {exc.lineno})"]) from exc
    visitor = _Visitor()
    visitor.visit(tree)
    visitor.report.lines = code.count("\n") + 1
    if visitor.report.max_loop_depth > 3:
        visitor.findings.append("loop nesting deeper than 3")
    if visitor.findings:
        raise ScanError(visitor.findings)
    return visitor.report
