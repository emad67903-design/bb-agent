"""
Implements: Section 10.7 test coverage -- core/sandbox/sandbox_validator.py

Every case here was first proven out as a one-off smoke-test script
during this component's design, per this codebase's established
discipline ("smoke-test every new function against a real input before
writing its test file"). This file is that coverage made permanent --
none of it should be treated as newly invented; it is a transcription.
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

import io
import sys

import pytest

from core.sandbox.sandbox_validator import (
    BLOCKED_BUILTINS,
    BLOCKED_MODULES,
    build_restricted_globals,
    raise_if_invalid,
    validate_code,
)
from core.sandbox.sandbox_validator import SandboxValidationError


def _assert_valid(code: str) -> None:
    result = validate_code(code)
    assert result.is_valid, f"expected valid, got violations: {result.violations}"
    assert result.violations == []


def _assert_invalid(code: str, *, contains: str | None = None) -> None:
    result = validate_code(code)
    assert not result.is_valid, "expected invalid, but validate_code accepted it"
    assert result.violations != []
    if contains is not None:
        assert any(contains in v for v in result.violations), (
            f"expected a violation containing {contains!r}, got {result.violations}"
        )


class TestLiteralBlueprintBlockedModules:
    """Section 10.7's BLOCKED set, direct imports."""

    @pytest.mark.parametrize(
        "module_name",
        [
            "socket",
            "socketserver",
            "urllib",
            "requests",
            "httpx",
            "aiohttp",
            "ftplib",
            "smtplib",
            "telnetlib",
            "ssl",
            "importlib",
            "ctypes",
            "mmap",
            "pickle",
            "marshal",
        ],
    )
    def test_bare_import_blocked(self, module_name):
        _assert_invalid(f"import {module_name}", contains=module_name)

    def test_http_client_dotted_import_blocked(self):
        _assert_invalid("from http.client import HTTPConnection", contains="http.client")

    def test_http_client_via_import_blocked(self):
        _assert_invalid("import http.client", contains="http.client")

    def test_http_server_not_blocked(self):
        """Section 10.7 lists `http.client`, not `http`. `http.server`
        is used elsewhere in this project (Section 3's
        `webhook_trigger.py`) and must not be caught by a naive
        truncation of the dotted name to its root."""
        _assert_valid("from http.server import HTTPServer")


class TestLiteralBlueprintBlockedDottedCalls:
    """Section 10.7's six named dotted calls, including the two evasions
    a naive checker would miss: import aliasing and from-import to a
    bare name."""

    def test_os_system_direct(self):
        _assert_invalid('import os\nos.system("ls")', contains="os")

    def test_os_system_aliased_import(self):
        _assert_invalid('import os as o\no.system("ls")', contains="os.system")

    def test_os_popen_aliased(self):
        _assert_invalid('import os as o\no.popen("ls")', contains="os.popen")

    def test_os_execv_aliased(self):
        _assert_invalid('import os as o\no.execv("/bin/ls", [])', contains="os.execv")

    def test_subprocess_run_direct(self):
        _assert_invalid('import subprocess\nsubprocess.run(["ls"])', contains="subprocess")

    def test_subprocess_popen_aliased(self):
        _assert_invalid('import subprocess as sp\nsp.Popen(["ls"])', contains="subprocess.Popen")

    def test_subprocess_call_aliased(self):
        _assert_invalid('import subprocess as sp\nsp.call(["ls"])', contains="subprocess.call")

    def test_from_os_import_system_bare_name_evasion(self):
        _assert_invalid('from os import system\nsystem("ls")', contains="os")

    def test_from_os_import_system_as_alias_evasion(self):
        _assert_invalid('from os import system as s\ns("ls")', contains="os")

    def test_from_subprocess_import_run_bare_name_evasion(self):
        _assert_invalid('from subprocess import run\nrun(["ls"])', contains="subprocess")

    def test_import_alone_without_the_dangerous_call_still_blocked_by_module_rule(self):
        """os.path.join alone would not trip the dotted-call check, but
        the whole-module hardening rule (below) blocks `import os`
        itself regardless."""
        _assert_invalid("import os\nprint(os.path.join('a', 'b'))")


class TestBlockedBuiltins:
    """Section 10.7: eval, exec, compile, open, __import__ -- referenced,
    not just called."""

    @pytest.mark.parametrize("name", sorted(BLOCKED_BUILTINS))
    def test_direct_call_blocked(self, name):
        _assert_invalid(f'{name}("x")' if name != "open" else 'open("/etc/passwd")', contains=name)

    def test_eval_referenced_not_called(self):
        _assert_invalid("x = eval", contains="eval")

    def test_open_referenced_not_called(self):
        _assert_invalid("f = open", contains="open")

    def test_compile_call_blocked(self):
        _assert_invalid('compile("1", "<s>", "eval")', contains="compile")

    def test_dunder_import_call_blocked(self):
        _assert_invalid('__import__("os")', contains="__import__")


class TestHardeningWholeModuleBlocks:
    """This file's own additions beyond Section 10.7's literal list:
    os/subprocess as whole modules, plus pathlib/shutil/io/tempfile/sys
    -- each reaching a category the blocklist is already visibly trying
    to close (module docstring explains the reasoning for each)."""

    def test_bare_import_os_no_dangerous_call(self):
        _assert_invalid("import os\nprint(os.getcwd())")

    def test_bare_import_subprocess_unused(self):
        _assert_invalid("import subprocess")

    def test_pathlib_read_text_bypasses_open(self):
        _assert_invalid('from pathlib import Path\nPath("/etc/passwd").read_text()')

    def test_shutil_rmtree(self):
        _assert_invalid('import shutil\nshutil.rmtree("/")')

    def test_io_open_is_the_same_open(self):
        _assert_invalid('import io\nio.open("/etc/passwd")')

    def test_tempfile_import_alone(self):
        _assert_invalid("import tempfile")

    def test_sys_import_alone(self):
        _assert_invalid("import sys")

    def test_sys_modules_bypass_of_whole_module_os_block(self):
        """The gap this addition specifically closes: the harness
        itself transitively loads os/subprocess via multiprocessing, so
        they are already present in sys.modules inside the child --
        `import sys` was the only remaining path to them that neither
        the os/subprocess whole-module block nor the dotted-call check
        touches, since this never writes "os" or "subprocess" next to
        an `import` keyword anywhere."""
        _assert_invalid('import sys\nsys.modules["os"].system("ls")', contains="sys")

    def test_from_sys_import_modules_bypass(self):
        _assert_invalid('from sys import modules\nmodules["os"]', contains="sys")


class TestDunderAttributeBlocking:
    """This file's own addition: all dunder attribute access blocked,
    the standard mitigation against object-introspection sandbox
    escapes that never name a blocked identifier."""

    def test_class_bases_subclasses_introspection_chain(self):
        _assert_invalid("x = ().__class__.__bases__[0].__subclasses__()")

    def test_globals_via_function_dunder(self):
        _assert_invalid("def f(): pass\nf.__globals__")

    def test_traceback_frame_globals(self):
        _assert_invalid(
            "try:\n    1/0\nexcept Exception as e:\n    e.__traceback__.tb_frame.f_globals"
        )

    def test_code_dunder(self):
        _assert_invalid("def f(): pass\nf.__code__")

    def test_dunder_on_a_plain_literal(self):
        _assert_invalid("(1).__class__")


class TestSyntaxErrorsFailClosed:
    def test_unparseable_code_is_invalid_not_a_crash(self):
        result = validate_code("def f(:\n  pass")
        assert result.is_valid is False
        assert len(result.violations) == 1
        assert "does not parse" in result.violations[0]

    def test_validate_code_never_raises_on_bad_syntax(self):
        # The function-under-test itself must not raise; only
        # raise_if_invalid should.
        validate_code("this is not python at all !!! ###")


class TestLegitimateCodeAccepted:
    """False-positive checks -- the sandbox's actual, narrow purpose
    (call_target + string/JSON/regex manipulation + print) must not be
    collateral damage from the hardening layers above."""

    def test_empty_script(self):
        _assert_valid("")

    def test_print_only(self):
        _assert_valid('print("hello world")')

    def test_math_and_fstring(self):
        _assert_valid('x = 7 * 7\nprint(f"answer is {x}")')

    def test_json_and_re_and_call_target(self):
        _assert_valid(
            """
import json
import re
data = {"a": 1, "b": [1, 2, 3]}
s = json.dumps(data)
m = re.search(r"XBOW_(\\d+)", "XBOW_12345 probe result")
print("result:", m.group(1) if m else None)
result = call_target("https://example.com/api")
print("status:", result["status"])
"""
        )

    def test_ordinary_class_definition_no_dunder_access(self):
        _assert_valid(
            """
class Probe:
    def __init__(self, value):
        self.value = value

    def describe(self):
        return f"probe={self.value}"

p = Probe(42)
print(p.describe())
"""
        )

    def test_list_dict_comprehensions_and_string_methods(self):
        _assert_valid(
            """
words = ["Foo", "BAR", "baz"]
lowered = [w.lower() for w in words]
counts = {w: len(w) for w in lowered}
print(counts)
"""
        )


class TestRaiseIfInvalid:
    def test_raises_on_invalid_code(self):
        with pytest.raises(SandboxValidationError, match="socket"):
            raise_if_invalid("import socket")

    def test_does_not_raise_on_valid_code(self):
        raise_if_invalid('print("fine")')

    def test_message_joins_all_violations_not_just_the_first(self):
        with pytest.raises(SandboxValidationError) as excinfo:
            raise_if_invalid("import socket\nimport pickle\neval('1')")
        message = str(excinfo.value)
        assert "socket" in message
        assert "pickle" in message
        assert "eval" in message


class TestBuildRestrictedGlobalsRuntimeDefenseInDepth:
    """The __import__-strip-vs-guard investigation, made permanent:
    plain removal breaks ordinary `import` statements (Python's compiler
    implicitly calls __builtins__.__import__ for every import
    statement); leaving the real function in place reopens a dynamic
    bypass via globals()["__builtins__"]["__import__"]. A guarded
    wrapper is the only combination that is both fully functional for
    allowed modules and fully closed for blocked ones -- proven with the
    same four adversarial cases (A-D) used during design."""

    def _run(self, code: str, scope_domains: list[str] | None = None) -> tuple[str, str, str]:
        """Executes code with a real build_restricted_globals() namespace
        and returns (outcome, message, captured_stdout). outcome is
        "completed" or the exception's type name."""
        g = build_restricted_globals(scope_domains or ["*.example.com"])
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            exec(compile(code, "<test>", "exec"), g)
            return ("completed", "", buf.getvalue())
        except BaseException as exc:  # noqa: BLE001 -- deliberately broad, mirrors _sandboxed_worker's own boundary
            return (type(exc).__name__, str(exc), buf.getvalue())
        finally:
            sys.stdout = old_stdout

    def test_A_ordinary_import_of_an_allowed_module_works(self):
        """A plain strip of __import__ broke this -- caught by this
        exact case during design, not by reasoning alone."""
        outcome, _msg, stdout = self._run(
            'import json\nprint(json.dumps({"a": 1}))'
        )
        assert outcome == "completed"
        assert stdout == '{"a": 1}\n'

    def test_B_dynamic_import_bypass_of_a_blocked_module_is_closed(self):
        """The gap a plain (unguarded) restoration of __import__ would
        reopen: reaching a blocked module via globals()["__builtins__"]
        ["__import__"](...) instead of a static `import` statement,
        which validate_code cannot see."""
        outcome, msg, _stdout = self._run(
            'm = globals()["__builtins__"]["__import__"]("os")\nprint(m)'
        )
        assert outcome == "ImportError"
        assert "os" in msg

    def test_C_dynamic_import_of_an_allowed_module_still_works(self):
        """Confirms the guard is selective -- it blocks by module name,
        not by blocking the dynamic-import mechanism itself."""
        outcome, _msg, stdout = self._run(
            'm = globals()["__builtins__"]["__import__"]("json")\n'
            'print(m.dumps({"ok": True}))'
        )
        assert outcome == "completed"
        assert stdout == '{"ok": true}\n'

    def test_D_eval_exec_open_input_genuinely_absent_at_runtime(self):
        for name, code in [
            ("eval", 'eval("1")'),
            ("exec", 'exec("x=1")'),
            ("open", 'open("/etc/passwd")'),
            ("input", "input()"),
        ]:
            outcome, msg, _stdout = self._run(code)
            assert outcome == "NameError", f"{name}: expected NameError, got {outcome}: {msg}"
            assert name in msg

    def test_call_target_present_and_callable_in_the_namespace(self):
        g = build_restricted_globals(["*.example.com"])
        assert "call_target" in g
        assert callable(g["call_target"])

    def test_print_len_str_still_available(self):
        outcome, _msg, stdout = self._run('print(len("hello"), str(42))')
        assert outcome == "completed"
        assert stdout == "5 42\n"


class TestModuleConstantsAreConsistentWithEachOther:
    """Cheap arithmetic/consistency guards -- catches a drift between
    the two blocked-module sets or a typo introduced in a future edit,
    without needing to enumerate every module again."""

    def test_blocked_modules_is_the_union_of_literal_and_hardening_sets(self):
        assert "socket" in BLOCKED_MODULES  # literal
        assert "os" in BLOCKED_MODULES  # hardening
        assert "sys" in BLOCKED_MODULES  # hardening

    def test_no_overlap_between_blocked_builtins_and_normal_usage_names(self):
        # A guard against accidentally blocking something as broadly
        # useful as `print` or `len` in a future edit.
        assert "print" not in BLOCKED_BUILTINS
        assert "len" not in BLOCKED_BUILTINS
        assert "str" not in BLOCKED_BUILTINS
