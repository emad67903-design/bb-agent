"""
Implements: test coverage for core/scanners/content_heuristics.py
(authored, not blueprint-cited -- docs/DECISIONS.md item 78).
Blueprint: bb_agent_v6.6_final_blueprint.md
"""

from __future__ import annotations

from core.scanners.content_heuristics import looks_like_hostname_content, looks_like_win_ini_content


class TestLooksLikeHostnameContent:
    def test_plausible_hostname_differing_from_baseline_passes(self):
        assert looks_like_hostname_content("web-server-01", "<html>404</html>") is True

    def test_hostname_with_dots_passes(self):
        assert looks_like_hostname_content("db.prod.internal.example", "baseline") is True

    def test_identical_to_baseline_fails(self):
        assert looks_like_hostname_content("same content", "same content") is False

    def test_identical_after_stripping_whitespace_fails(self):
        assert looks_like_hostname_content("  same  ", "same") is False

    def test_empty_after_stripping_fails(self):
        assert looks_like_hostname_content("   ", "baseline") is False

    def test_longer_than_253_chars_fails(self):
        assert looks_like_hostname_content("a" * 254, "baseline") is False

    def test_253_char_multi_label_hostname_of_valid_shape_passes(self):
        """Boundary check using a REAL valid hostname shape -- labels
        each <= 63 chars (RFC 1035's per-label limit, which this regex
        enforces via {0,61} plus the two required end characters),
        joined with dots to reach exactly 253 total. A single 253-char
        label is NOT valid shape (see the dedicated test below) --
        multi-label is required to reach this total under the per-label
        limit."""
        labels = ["a" * 63, "b" * 63, "c" * 63]
        hostname = ".".join(labels)
        hostname += "." + "d" * (253 - len(hostname) - 1)  # pad the final label to hit 253 exactly
        assert len(hostname) == 253
        assert looks_like_hostname_content(hostname, "baseline") is True

    def test_single_label_over_63_chars_fails_shape_check(self):
        """RFC 1035: each dot-separated label is capped at 63 characters
        -- a single label longer than that is not a valid hostname
        shape, even though it is well under the 253-char TOTAL cap this
        module also enforces separately."""
        assert looks_like_hostname_content("a" * 64, "baseline") is False

    def test_html_content_fails_shape_check(self):
        assert looks_like_hostname_content("<html><body>error</body></html>", "baseline") is False

    def test_content_with_spaces_fails_shape_check(self):
        assert looks_like_hostname_content("this is a sentence", "baseline") is False

    def test_content_with_underscore_fails_shape_check(self):
        """RFC 1035 hostnames don't include underscores -- a real
        constraint this heuristic enforces, not an oversight."""
        assert looks_like_hostname_content("some_host_name", "baseline") is False


class TestLooksLikeWinIniContent:
    _WIN_INI = "[fonts]\n[extensions]\nEPSF.SHW=pscript.hlp"

    def test_real_win_ini_content_passes(self):
        assert looks_like_win_ini_content(self._WIN_INI, "<html>404</html>") is True

    def test_case_insensitive(self):
        assert looks_like_win_ini_content("[FONTS]\n[EXTENSIONS]\nkey=val", "baseline") is True

    def test_missing_extensions_section_fails(self):
        assert looks_like_win_ini_content("[fonts]\nkey=val", "baseline") is False

    def test_missing_fonts_section_fails(self):
        assert looks_like_win_ini_content("[extensions]\nkey=val", "baseline") is False

    def test_identical_to_baseline_fails(self):
        assert looks_like_win_ini_content(self._WIN_INI, self._WIN_INI) is False

    def test_empty_after_stripping_fails(self):
        assert looks_like_win_ini_content("   ", "baseline") is False

    def test_unrelated_content_fails(self):
        assert looks_like_win_ini_content("<html>normal page</html>", "baseline") is False

    def test_no_length_cap_unlike_hostname_check(self):
        """win.ini files are legitimately larger than a hostname file --
        this check has no equivalent MAX_HOSTNAME_LENGTH-style cap,
        deliberately (module docstring: exact-substring match, not a
        shape approximation that needs a plausibility bound)."""
        long_content = self._WIN_INI + ("\n; comment padding" * 50)
        assert looks_like_win_ini_content(long_content, "baseline") is True
