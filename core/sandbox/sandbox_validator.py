"""
Implements: Section 3 -- core/sandbox/sandbox_validator.py ("AST
blocklist; call_target() injected into namespace"), and Section 10.7's
full spec:

    **AST blocklist (immediate rejection):**
    BLOCKED = {
        'os.system', 'os.popen', 'os.execv', 'subprocess.call', 'subprocess.Popen',
        'subprocess.run', 'socket', 'socketserver', 'urllib', 'requests', 'httpx', 'aiohttp',
        'ftplib', 'smtplib', 'http.client', 'telnetlib', 'ssl',
        '__import__', 'eval', 'exec', 'compile', 'importlib',
        'open',      # ALL modes blocked; output via print() only
        'ctypes', 'mmap', 'pickle', 'marshal',
    }
Blueprint: bb_agent_v6.6_final_blueprint.md

THIS IS THE HIGHEST-STAKES FILE IN THE PROJECT SO FAR (this week's own
kickoff prompt, verbatim: "a design mistake means arbitrary code
execution outside the intended boundary, not just a wrong test result").
Every design decision below is documented at the point it's made, not
just here, but the honest summary belongs at the top:

WHAT THIS FILE DOES GUARANTEE: every name literally listed in Section
10.7's BLOCKED set is rejected before any subprocess is even spawned --
including through the two most obvious literal-name evasions (import
aliasing: `import os as o; o.system(...)`; and `from`-import-to-bare-
name: `from os import system; system(...)`), both of which a naive
substring or single-AST-node-type check would miss. `docs/DECISIONS.md`
item 66 records exactly which evasions were considered and defended
against.

WHAT THIS FILE DOES NOT, AND CANNOT, GUARANTEE: Section 13 rejected a
Docker sandbox in favor of subprocess + AST for this hardware's RAM
budget -- a decision this file implements, not one it revisits. But pure
AST/builtins blocklisting of a full general-purpose language is a known-
incomplete mitigation against a *deliberately adversarial* script, not
just this implementation's incompleteness: Python's own object
introspection (`().__class__.__bases__[0].__subclasses__()` and similar)
can reach dangerous functionality without ever naming a blocked
identifier in source text at all. This file blocks all dunder attribute
access specifically because it is the single highest-leverage mitigation
against that class of technique (below), but does not claim to close it
completely -- no pure-AST approach can, against a sufficiently determined
adversary, without also restricting the interpreter itself (a real
restricted-execution interpreter, or OS-level isolation Section 13 has
already ruled out for this hardware). The 30-second timeout and 128 MB
RAM ceiling (`code_executor.py`) and the network-egress restriction
(`safety_guard.py`) are the containment backstops for this residual
risk: even a script that fully escapes the intended boundary is still a
short-lived, memory-capped OS process that cannot reach the network
except through `call_target()`'s own gate -- UNLESS it also manages to
reach a raw networking primitive directly, which is the one category
this file tries hardest to close off completely (see MODULE-LEVEL BLOCK
below). This is stated plainly, not to undermine the work, but because
overclaiming a security boundary is itself a security bug.

TWO LAYERS BEYOND SECTION 10.7'S LITERAL LIST, BOTH DOCUMENTED JUDGMENT
CALLS (docs/DECISIONS.md item 66), NOT SILENT INVENTION:

1. MODULE-LEVEL BLOCK, NOT JUST THE SIX NAMED DOTTED CALLS: Section
   10.7 lists `os.system`, `os.popen`, `os.execv`, `subprocess.call`,
   `subprocess.Popen`, `subprocess.run` as the specific dangerous calls
   -- but never says `import os` or `import subprocess` alone are fine.
   Given this sandbox's stated purpose ("LLM-generated Python PoC
   verification scripts only" -- Section 10.7's first line) has no
   legitimate use for EITHER module at all (no PoC verifying an HTTP
   response needs process management or raw file-descriptor work), and
   given that allowing `import os` while only blocking three of its
   dozens of members leaves every OTHER os.* escape hatch open
   (`os.spawnv`, `os.posix_spawn`, `os.fork`, ...) as well as the
   `from os import system` bare-name evasion described above -- this
   file blocks `os` and `subprocess` as whole modules. The same
   reasoning extends to four modules Section 10.7 never mentions at all
   but that reach the identical two categories (filesystem access
   without the blocked `open()` builtin, in this case) the blocklist is
   visibly trying to close: `pathlib` (`Path(...).read_text()` /
   `.write_text()` / `.unlink()` bypass `open()` entirely -- they are
   not implemented in terms of the builtin), `shutil` (`rmtree`, file
   copy/move), `io` (`io.open` IS the builtin `open`, just reached via
   a different name), and `tempfile` (creates real files on disk).
   A FIFTH module, `sys`, is blocked for a distinct, empirically-found
   reason, not the same file-access reasoning as the other four:
   `code_executor.py` (the harness) must import `multiprocessing`, which
   transitively imports `os` and `subprocess` for its own OS-level
   process management -- verified directly, not assumed: a forked child
   process was checked and found to already have both sitting in
   `sys.modules`, regardless of what the untrusted script itself ever
   imports. `import sys; sys.modules["os"].system(...)` writes neither
   "os" nor "subprocess" next to an `import` keyword anywhere in the
   script, so the whole-module block on `os`/`subprocess` above does
   nothing to stop it -- it never imports either module, it just looks
   one up that the harness already loaded. Blocking whole modules
   Section 10.7 already effectively intends to block (via `os.system`
   et al.) as a full module, plus the five modules doing exactly what
   `open` already isn't allowed to do or what a from-import evasion
   would otherwise reach, is closing a gap in the stated goal, not
   adding a new one -- none of the five extra modules appear in Section
   10.7's own
   text and are flagged as this file's own addition for that reason.

2. ALL DUNDER ATTRIBUTE ACCESS BLOCKED: not in Section 10.7 at all.
   `.__class__`, `.__bases__`, `.__subclasses__`, `.__globals__`,
   `.__code__`, `.__closure__`, `.__builtins__`, and every other dunder
   attribute are the standard toolkit for walking from an ordinary,
   permitted object (e.g. the integer literal `0`, or any string) to
   dangerous functionality without ever writing a blocked identifier's
   name in the source. A narrowly-scoped list of "the specific dunders
   known to be dangerous" is itself a losing game (an incomplete list is
   a security bug); blocking the whole category is the standard
   mitigation used by comparable tools (e.g. RestrictedPython). A PoC-
   verification script doing string/JSON/regex manipulation and calling
   `call_target()` has no legitimate reason to reference any dunder
   attribute explicitly, so the false-positive cost of this blanket rule
   is expected to be at or near zero for this sandbox's actual, narrow
   purpose.

BOTH ARE HARD ADDITIONS TO SECTION 10.7'S LIST, NOT REPLACEMENTS FOR IT
-- `_LITERAL_BLUEPRINT_BLOCKED_DOTTED_CALLS` below transcribes Section
10.7's six named dotted calls verbatim and independently of the
whole-module block, so that even if the whole-module reasoning above
were ever relaxed, the six explicitly-named calls remain caught on their
own.
"""

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass

from core.sandbox.safety_guard import build_call_target

# Section 10.7, verbatim: modules for which ANY import is rejected --
# no legitimate PoC-verification use, and each is either a raw network
# primitive/library or a dynamic-code/introspection primitive.
_LITERAL_BLUEPRINT_BLOCKED_MODULES = frozenset(
    {
        "socket",
        "socketserver",
        "urllib",
        "requests",
        "httpx",
        "aiohttp",
        "ftplib",
        "smtplib",
        "http.client",
        "telnetlib",
        "ssl",
        "importlib",
        "ctypes",
        "mmap",
        "pickle",
        "marshal",
    }
)

# This file's own hardening addition (module docstring, point 1) --
# whole-module blocks for os/subprocess (Section 10.7 names only six of
# their members) plus four filesystem-access modules Section 10.7 never
# mentions but that reach exactly what `open()` already isn't allowed
# to, PLUS `sys` (empirically verified, not just reasoned about: the
# harness process itself -- code_executor.py -- must import
# `multiprocessing`, which transitively imports `os` and `subprocess`
# for its own OS-level process management; a forked child therefore has
# BOTH already sitting in `sys.modules` regardless of what the untrusted
# script itself is permitted to import. `import sys; sys.modules["os"].
# system(...)` requires writing neither "os" nor "subprocess" next to an
# `import` keyword anywhere in the script -- the whole-module block on
# `os`/`subprocess` above does nothing to stop it, since this path never
# imports either module itself, it just looks one up that's already
# loaded. Blocking `sys` closes the only way this codebase's own
# validated, otherwise-legitimate PoC scripts have of reaching
# `sys.modules` at all -- and `sys` has no other legitimate use in this
# sandbox's narrow, stated purpose (call_target + string/JSON/regex
# manipulation + print) to weigh against closing it.
_HARDENING_BLOCKED_MODULES = frozenset(
    {"os", "subprocess", "pathlib", "shutil", "io", "tempfile", "sys"}
)

BLOCKED_MODULES = _LITERAL_BLUEPRINT_BLOCKED_MODULES | _HARDENING_BLOCKED_MODULES

# Section 10.7, verbatim: the six specific dotted calls, kept as their
# own check independent of the whole-module block above (module
# docstring explains why both exist).
_LITERAL_BLUEPRINT_BLOCKED_DOTTED_CALLS = frozenset(
    {
        ("os", "system"),
        ("os", "popen"),
        ("os", "execv"),
        ("subprocess", "call"),
        ("subprocess", "Popen"),
        ("subprocess", "run"),
    }
)

# Section 10.7, verbatim: builtins rejected wherever referenced (not
# just called -- `x = eval` must be rejected too, since `x` could be
# called later, passed to another function, etc.).
BLOCKED_BUILTINS = frozenset({"__import__", "eval", "exec", "compile", "open"})

# The exact set of builtins actually REMOVED from the executed code's
# runtime namespace (module docstring: defense-in-depth belt-and-
# suspenders in case some evasion the AST pass does not catch still
# reaches one of these by name at runtime). `__import__` is deliberately
# EXCLUDED from this set -- see `_build_guarded_import`'s docstring for
# why it needs a guarded replacement instead of outright removal. A
# superset of BLOCKED_BUILTINS minus `__import__`: `input` is added
# (reads from the sandbox's stdin, which the harness does not provide
# meaningfully and could hang the subprocess waiting on it -- redundant
# with the 30s timeout as a backstop, but there is no reason to leave it
# reachable).
_RUNTIME_STRIPPED_BUILTINS = (BLOCKED_BUILTINS - {"__import__"}) | {"input"}


class SandboxValidationError(Exception):
    """Raised by `raise_if_invalid` (never by `validate_code`, which
    returns a result instead of raising -- see that function's
    docstring for why both forms exist)."""


@dataclass(frozen=True)
class ValidationResult:
    """The outcome of one `validate_code` call.

    Attributes:
        is_valid: `True` iff `violations` is empty AND the code parsed
            as valid Python at all (a `SyntaxError` is itself treated as
            invalid -- fail closed on code this checker cannot even
            analyze, never fail open).
        violations: Human-readable descriptions of every distinct
            problem found, in source order. Empty iff `is_valid`.
    """

    is_valid: bool
    violations: list[str]


def _iter_import_module_roots(node: ast.Import | ast.ImportFrom) -> list[tuple[str, str | None]]:
    """Extracts `(dotted_module_name, alias_or_none)` pairs from one
    Import/ImportFrom node.

    For `ast.Import` (`import a.b.c as x`), returns `[("a.b.c", "x")]`.
    For `ast.ImportFrom` (`from a.b import c as x`), returns
    `[("a.b", "x")]` if `a.b` itself is what matters (module-level
    blocking checks this), keyed by the FROM-module, not the imported
    name -- the imported *name* is handled separately by
    `_iter_from_import_names`, since `from os import system` needs to be
    caught even though `os` alone is already whole-module-blocked (two
    independent, overlapping checks, deliberately -- see module
    docstring on why the literal six-dotted-call check stays independent
    of the whole-module check).
    """
    if isinstance(node, ast.Import):
        return [(alias.name, alias.asname) for alias in node.names]
    # ast.ImportFrom
    if node.module is None:
        # `from . import x` (relative import, no module name) -- nothing
        # in BLOCKED_MODULES can match a None module name.
        return []
    return [(node.module, None)]


def _module_root(dotted_name: str) -> str:
    """`"os.path"` -> `"os"`; `"http.client"` -> `"http.client"` (this
    one IS the exact blocked entry, Section 10.7 lists it dotted, so no
    truncation to `"http"` alone -- `http.server` (used elsewhere in
    this codebase, Section 3's `webhook_trigger.py`) must NOT be
    blocked)."""
    if dotted_name in BLOCKED_MODULES:
        return dotted_name
    return dotted_name.split(".")[0]


def _iter_from_import_names(node: ast.ImportFrom) -> list[tuple[str, str, str | None]]:
    """For `from X import name [as alias]`, returns
    `[(X, name, alias_or_none), ...]` -- used to catch
    `from os import system` (a bare-name evasion of the dotted-call
    check: after this import, `system` is callable directly, with no
    `os.` prefix anywhere in the call site for a naive checker to spot)."""
    if node.module is None:
        return []
    return [(node.module, alias.name, alias.asname) for alias in node.names]


class _Visitor(ast.NodeVisitor):
    """Walks the full AST once, collecting every distinct violation.
    Tracks import aliases (`import os as o`) so a later `o.system(...)`
    call is still recognized as `os.system` -- a plain substring or
    single-node-shape check would miss this."""

    def __init__(self) -> None:
        self.violations: list[str] = []
        # alias -> real dotted module name, e.g. {"o": "os", "sp": "subprocess"}
        self._module_aliases: dict[str, str] = {}
        # bare name -> (module, original_name), e.g. {"system": ("os", "system")}
        # for `from os import system` / `from os import system as sys_call`
        self._from_import_aliases: dict[str, tuple[str, str]] = {}

    def visit_Import(self, node: ast.Import) -> None:
        for dotted_name, alias in _iter_import_module_roots(node):
            root = _module_root(dotted_name)
            if root in BLOCKED_MODULES or dotted_name in BLOCKED_MODULES:
                self.violations.append(f"blocked import: {dotted_name}")
            local_name = alias if alias is not None else dotted_name.split(".")[0]
            self._module_aliases[local_name] = dotted_name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for dotted_name, _alias in _iter_import_module_roots(node):
            root = _module_root(dotted_name)
            if root in BLOCKED_MODULES or dotted_name in BLOCKED_MODULES:
                self.violations.append(f"blocked import: from {dotted_name} import ...")
        for module, name, alias in _iter_from_import_names(node):
            if (module, name) in _LITERAL_BLUEPRINT_BLOCKED_DOTTED_CALLS:
                local_name = alias if alias is not None else name
                self.violations.append(f"blocked import: from {module} import {name}")
                self._from_import_aliases[local_name] = (module, name)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in BLOCKED_BUILTINS:
            self.violations.append(f"blocked name referenced: {node.id}")
        elif node.id in self._from_import_aliases:
            module, name = self._from_import_aliases[node.id]
            self.violations.append(f"blocked name referenced: {module}.{name} (imported as {node.id})")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__") and node.attr.endswith("__"):
            self.violations.append(f"blocked dunder attribute access: .{node.attr}")
        elif isinstance(node.value, ast.Name) and node.value.id in self._module_aliases:
            real_module = self._module_aliases[node.value.id]
            if (real_module, node.attr) in _LITERAL_BLUEPRINT_BLOCKED_DOTTED_CALLS:
                self.violations.append(f"blocked call: {real_module}.{node.attr}")
        self.generic_visit(node)


def validate_code(code: str) -> ValidationResult:
    """Section 10.7's AST blocklist: parses `code` and rejects it if it
    references anything in `BLOCKED_MODULES`, `BLOCKED_BUILTINS`, or the
    six literal dotted calls -- including through import-alias and
    from-import-to-bare-name evasions -- or blocks any dunder attribute
    access (this file's own hardening addition, module docstring point
    2).

    This function never raises for ordinary bad input; a `SyntaxError`
    from `ast.parse` itself becomes a violation, not an exception --
    code this checker cannot even parse is rejected the same as code it
    parses and finds a problem in (fail closed either way). Use
    `raise_if_invalid` instead when a raise-on-failure call site is more
    convenient than checking `.is_valid`.

    Args:
        code: The LLM-generated Python source to check.

    Returns:
        A `ValidationResult`. `is_valid=False` with one or more entries
        in `violations` covers both "found a blocked reference" and
        "could not parse at all."
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return ValidationResult(is_valid=False, violations=[f"code does not parse as valid Python: {exc}"])

    visitor = _Visitor()
    visitor.visit(tree)
    return ValidationResult(is_valid=len(visitor.violations) == 0, violations=visitor.violations)


def raise_if_invalid(code: str) -> None:
    """`validate_code`, raising `SandboxValidationError` instead of
    returning a result -- for call sites (e.g. `code_executor.py`) that
    want to short-circuit on rejection rather than branch on
    `.is_valid`.

    Args:
        code: The LLM-generated Python source to check.

    Raises:
        SandboxValidationError: If `validate_code(code).is_valid` is
            `False`. The exception message joins every violation found,
            not just the first -- a rejected script's author (human or
            LLM) benefits from seeing everything wrong at once.
    """
    result = validate_code(code)
    if not result.is_valid:
        raise SandboxValidationError("; ".join(result.violations))


def _build_guarded_import():
    """Returns a drop-in replacement for the builtin `__import__`,
    delegating to the real one only for non-`BLOCKED_MODULES` names.

    WHY THIS EXISTS, NOT A PLAIN STRIP LIKE THE OTHER FOUR BLOCKED
    BUILTINS: caught by this file's own pre-commit smoke test, not by
    reasoning alone -- a first version simply removed `__import__` from
    the restricted builtins dict, the same treatment as `eval`/`exec`/
    `compile`/`open`. That broke every ordinary `import` STATEMENT in
    validated, otherwise-legitimate code (`import json` included):
    Python's compiler translates `import X` into an implicit call to
    `__builtins__.__import__(...)`, invisible in the source text, so
    removing the name entirely does not just block dynamic
    `__import__("os")`-style calls (which `validate_code` already
    catches statically, by name, as a `BLOCKED_BUILTINS` reference) -- it
    breaks the language's own import mechanism for every module,
    including ones this sandbox has no reason to forbid.

    Restoring the REAL `__import__` unmodified would fix that, but
    reopens a real gap: `validate_code`'s import checks are static
    (`ast.Import`/`ast.ImportFrom` nodes only) -- they cannot see a
    module name built or looked up at runtime, e.g.
    `globals()["__builtins__"]["__import__"]("os")`, which never writes
    the literal text "os" next to an `import` keyword anywhere
    `ast.parse` can see it statically. `globals` and `__builtins__` are
    both left reachable (blocking them would be a large false-positive
    cost against legitimate code for comparatively little gain, since
    stripping the *four* truly-optional dangerous builtins already
    removes what one would do with that access) -- so `__import__`
    itself has to be the thing that stays safe under indirect access,
    not the paths that might reach it.

    This wrapper is that fix: whatever *reaches* `__import__` --
    statement-level or fully dynamic -- it still refuses `BLOCKED_MODULES`
    by name at the moment of the call, which is the one thing indirect
    access cannot route around.
    """

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if _module_root(name) in BLOCKED_MODULES or name in BLOCKED_MODULES:
            raise ImportError(f"import blocked by sandbox policy: {name}")
        return real_import(name, globals, locals, fromlist, level)

    return guarded_import


def build_restricted_globals(
    scope_domains: list[str],
    *,
    transport: object | None = None,
) -> dict[str, object]:
    """Builds the `globals()` dict the validated code is `exec()`'d
    against: `eval`/`exec`/`compile`/`open`/`input` removed entirely,
    `__import__` replaced with `_build_guarded_import`'s module-checking
    wrapper (not removed -- see that function's docstring for why), and
    the one pre-approved function, `call_target` (Section 10.7), added.

    Args:
        scope_domains: The program's in-scope domain patterns, threaded
            through to `safety_guard.build_call_target` unchanged.
        transport: Threaded straight through to
            `safety_guard.build_call_target`'s own `transport` parameter
            (typed `object` here, not `httpx.AsyncBaseTransport`, so
            this module -- which the AST validator half of it has no
            other reason to depend on `httpx` -- does not need an
            `httpx` import just to spell the type; `safety_guard.py`
            still enforces the real type at the point it matters).
            `None` (the default) means real network I/O. Exists so
            `code_executor.py`'s own integration tests can mock only at
            the `httpx` transport layer, end to end through the real
            pipeline, instead of hand-substituting a fake `call_target`.

    Returns:
        A dict suitable as the `globals` argument to `exec()`. Contains
        `__builtins__` (a restricted copy) and `call_target`. Nothing
        else -- the executed code's own top-level assignments populate
        this same dict as it runs, which is normal and expected
        (`exec(code, restricted_globals)` uses one namespace for both
        globals and locals at module-code scope).
    """
    restricted_builtins = {
        name: getattr(builtins, name)
        for name in dir(builtins)
        if name not in _RUNTIME_STRIPPED_BUILTINS
    }
    restricted_builtins["__import__"] = _build_guarded_import()
    return {
        "__builtins__": restricted_builtins,
        "call_target": build_call_target(scope_domains, transport=transport),
    }
