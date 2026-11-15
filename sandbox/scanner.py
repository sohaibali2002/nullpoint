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

# Signal code is data transformation, not I/O.  These allowlists replace the old "block a few
# known-bad names" approach: an unknown method is rejected rather than trusted.
SAFE_METHODS: frozenset[str] = frozenset(
    {
        "T", "abs", "add", "agg", "aggregate", "align", "all", "any", "apply", "argmax",
        "argmin", "argsort", "around", "asfreq", "asof", "astype", "at", "between", "ceil",
        "clip", "columns", "combine_first", "concat", "convert_dtypes", "copy", "corr", "count",
        "cov", "cummax", "cummin", "cumprod", "cumsum", "describe", "diff", "div", "divide",
        "dot", "drop", "drop_duplicates", "dropna", "dtype", "duplicated", "empty", "eq", "equal",
        "ewm", "expanding", "exp", "ffill", "fillna", "filter", "first", "floordiv", "floor",
        "ge", "get", "groupby", "gt", "head", "iat", "idxmax", "idxmin", "iloc", "index",
        "interpolate", "isin", "isna", "isnull", "items", "keys", "kurt", "kurtosis", "last",
        "le", "loc", "log", "log1p", "map", "mask", "max", "mean", "median", "merge", "min",
        "mod", "mul", "multiply", "name", "ndim", "ne", "notna", "notnull", "nunique", "pct_change",
        "pipe", "pow", "prod", "quantile", "rank", "rdiv", "reindex", "rename", "replace",
        "reset_index", "rolling", "round", "rsub", "set_axis", "shape", "shift", "size", "skew",
        "sort_index", "sort_values", "sqrt", "squeeze", "std", "sub", "subtract", "sum", "tail",
        "to_frame", "to_numpy", "transform", "transpose", "truediv", "unique", "unstack", "values",
        "var", "where", "xs", "day", "month", "year", "weekday", "quarter", "date",
        "days_in_month", "is_month_end", "is_month_start", "normalize", "tz_localize",
        "tz_convert", "strftime", "to_period",
    }
)

SAFE_MODULE_ATTRIBUTES: dict[str, frozenset[str]] = {
    "pandas": frozenset(
        {
            "DataFrame", "Series", "Index", "MultiIndex", "Timestamp", "Timedelta", "NA", "NaT",
            "concat", "cut", "get_dummies", "isna", "merge", "notna", "qcut", "to_datetime",
            "to_numeric",
        }
    ),
    "numpy": frozenset(
        {
            "abs", "arange", "argmax", "argmin", "array", "asarray", "ceil", "clip", "cos",
            "cumprod", "cumsum", "diag", "diff", "dot", "e", "exp", "expm1", "eye", "float64",
            "floor", "full", "inf", "int64", "isfinite", "isnan", "linspace", "log", "log1p",
            "maximum", "mean", "median", "minimum", "nan", "nanmean", "nanstd", "ndarray", "ones",
            "percentile", "pi", "power", "prod", "quantile", "repeat", "round", "sign", "sin",
            "sqrt", "std", "sum", "tanh", "where", "zeros",
        }
    ),
    "math": frozenset({"ceil", "exp", "floor", "isfinite", "log", "log1p", "sqrt"}),
}

FORBIDDEN_ATTRIBUTE_PREFIXES = ("read_", "to_", "fromfile", "fromregex", "memmap", "save", "load")
FORBIDDEN_MODULE_PATHS = frozenset(
    {"io", "lib", "ctypeslib", "testing", "f2py", "distutils", "core", "util", "compat", "_libs"}
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
        # ``compile_signal`` injects these names even when the signal omits imports.
        self.module_aliases: dict[str, str] = {"np": "numpy", "pd": "pandas"}

    def _fail(self, node: ast.AST, message: str) -> None:
        self.findings.append(f"line {getattr(node, 'lineno', '?')}: {message}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.report.imports.append(alias.name)
            if not _module_allowed(alias.name):
                self._fail(node, f"import of blocked module {alias.name!r}")
            root = alias.name.split(".")[0]
            self.module_aliases[alias.asname or root] = root
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        self.report.imports.append(module)
        if not _module_allowed(module):
            self._fail(node, f"from-import of blocked module {module!r}")
        root = module.split(".")[0]
        for alias in node.names:
            self.module_aliases[alias.asname or alias.name] = root
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
        root = node.value.id if isinstance(node.value, ast.Name) else None
        module = self.module_aliases.get(root or "", root or "")
        if module in SAFE_MODULE_ATTRIBUTES:
            if node.attr in FORBIDDEN_MODULE_PATHS or node.attr.startswith(FORBIDDEN_ATTRIBUTE_PREFIXES):
                self._fail(node, f"module reach-through or I/O via {module}.{node.attr}")
            elif node.attr not in SAFE_MODULE_ATTRIBUTES[module]:
                self._fail(node, f"{module}.{node.attr} is not on the signal allowlist")
        elif node.attr in BLOCKED_ATTRIBUTES:
            self._fail(node, f"access to blocked attribute {node.attr!r}")
        elif node.attr.startswith(FORBIDDEN_ATTRIBUTE_PREFIXES):
            self._fail(node, f"I/O attribute {node.attr!r} is not permitted")
        elif node.attr.startswith("__"):
            self._fail(node, f"dunder attribute {node.attr!r} is not permitted")
        elif node.attr not in SAFE_METHODS:
            self._fail(node, f"attribute {node.attr!r} is not on the signal allowlist")
        elif node.attr in SUSPICIOUS_ATTRIBUTES:
            self.report.warnings.append(
                f"line {node.lineno}: attribute {node.attr!r} needs review"
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
