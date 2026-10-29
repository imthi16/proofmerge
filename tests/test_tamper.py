"""Inspect owned committed fixtures only; never import or execute their contents."""

import subprocess
import tokenize

import pytest

from proofmerge.context import get_context
from proofmerge.evidence import TamperFinding
from proofmerge.tamper import TamperError, find_tamper, run_base_tests_on_head


@pytest.fixture
def inspect_diff(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Fixture")

    def inspect(before, after, path="tests/test_example.py", dirty=None):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if before is not None:
            if isinstance(before, bytes):
                target.write_bytes(before)
            else:
                target.write_text(before)
        git("add", ".")
        git("commit", "--allow-empty", "-m", "base")
        base = git("rev-parse", "HEAD")
        if after is None:
            target.unlink()
        else:
            target.write_text(after)
        git("add", ".")
        git("commit", "--allow-empty", "-m", "head")
        context = get_context(tmp_path, base, "HEAD")
        if dirty is not None:
            target.write_text(dirty)
        return find_tamper(tmp_path, context)

    return inspect


TEST = "def test_example():\n    assert 1 == 1\n    assert 2 == 2\n"


def signals(findings):
    assert all(f.base_test_on_head_outcome is None for f in findings)
    return {(f.test_name, f.kind) for f in findings}


def test_deleted_file_and_function(inspect_diff):
    findings = inspect_diff(TEST, None)
    assert signals(findings) == {(None, "DELETED"), ("test_example", "DELETED")}
    assert all(f.test_path == "tests/test_example.py" for f in findings)


def test_deleted_function_in_retained_file(inspect_diff):
    assert ("test_example", "DELETED") in signals(inspect_diff(TEST, "# removed\n"))


@pytest.mark.parametrize(
    ("marker", "kind"),
    [
        ("import pytest\n@pytest.mark.skip(reason='later')\n", "SKIPPED"),
        ("import pytest as pt\n@pt.mark.skipif(True, reason='later')\n", "SKIPPED"),
        ("from pytest import mark as m\n@m.xfail\n", "XFAIL"),
        ("import unittest as ut\n@ut.skip('later')\n", "SKIPPED"),
        ("from unittest import expectedFailure as fail\n@fail\n", "XFAIL"),
    ],
)
def test_new_function_mark(inspect_diff, marker, kind):
    assert ("test_example", kind) in signals(inspect_diff(TEST, marker + TEST))


def test_existing_mark_not_new(inspect_diff):
    before = "import pytest\n@pytest.mark.skip\n" + TEST
    result = signals(inspect_diff(before, before.replace("2 == 2", "2 == 3")))
    assert ("test_example", "SKIPPED") not in result


def test_changed_skip_condition(inspect_diff):
    before = "import pytest\n@pytest.mark.skipif(False, reason='later')\n" + TEST
    result = signals(inspect_diff(before, before.replace("False", "True")))
    assert ("test_example", "SKIPPED") in result


def test_module_marks(inspect_diff):
    after = "import pytest\npytestmark = [pytest.mark.skip, pytest.mark.xfail]\n" + TEST
    result = signals(inspect_diff(TEST, after))
    assert {(None, "SKIPPED"), (None, "XFAIL")} <= result


def test_class_and_async_method(inspect_diff):
    before = "class TestShop:\n    async def test_price(self):\n        assert True\n"
    after = "import pytest\n@pytest.mark.xfail\n" + before.replace("assert True", "pass")
    result = signals(inspect_diff(before, after))
    assert {("TestShop", "XFAIL"), ("TestShop.test_price", "ASSERTION_REMOVED")} <= result


def test_removed_assertion(inspect_diff):
    result = signals(inspect_diff(TEST, TEST.replace("    assert 2 == 2\n", "")))
    assert ("test_example", "ASSERTION_REMOVED") in result


def test_class_assigned_mark(inspect_diff):
    before = "class TestShop:\n    def test_price(self):\n        assert True\n"
    after = "import pytest\n" + before.replace(
        "class TestShop:\n", "class TestShop:\n    pytestmark = pytest.mark.skip\n"
    )
    assert ("TestShop", "SKIPPED") in signals(inspect_diff(before, after))


def test_unittest_class_without_test_prefix(inspect_diff):
    before = (
        "from unittest import TestCase\nclass ShopChecks(TestCase):\n"
        "    def test_price(self):\n        assert True\n"
    )
    after = before.replace("assert True", "pass")
    assert ("ShopChecks.test_price", "ASSERTION_REMOVED") in signals(inspect_diff(before, after))


def test_repeated_assertion_removed(inspect_diff):
    result = signals(inspect_diff(TEST + "    assert 2 == 2\n", TEST))
    assert ("test_example", "ASSERTION_REMOVED") in result


def test_moved_assertions_not_removed(inspect_diff):
    after = "def test_example():\n    assert 2 == 2\n    assert 1 == 1\n"
    result = signals(inspect_diff(TEST, after))
    assert ("test_example", "MODIFIED") in result
    assert ("test_example", "ASSERTION_REMOVED") not in result


def test_changed_assertion_is_modified_not_removed(inspect_diff):
    # The demo's test-tamper PR rewrites `== 108.0` to `== 90.9`; no assertion is gone.
    result = signals(inspect_diff(TEST, TEST.replace("2 == 2", "2 == 3")))
    assert ("test_example", "MODIFIED") in result
    assert ("test_example", "ASSERTION_REMOVED") not in result


def test_renamed_marker_alias_is_not_a_new_skip(inspect_diff):
    before = "import pytest\n@pytest.mark.skip(reason='later')\n" + TEST
    after = "import pytest as pt\n@pt.mark.skip(reason='later')\n" + TEST
    result = signals(inspect_diff(before, after))
    assert ("test_example", "SKIPPED") not in result
    changed = before.replace("'later'", "'never'")
    assert ("test_example", "SKIPPED") in signals(inspect_diff(before, changed))


def test_nested_test_class_is_inspected(inspect_diff):
    before = (
        "class TestOuter:\n    class TestInner:\n"
        "        def test_price(self):\n            assert 1 == 1\n"
        "        def test_other(self):\n            assert 2 == 2\n"
    )
    after = before.replace("        def test_price(self):\n            assert 1 == 1\n", "")
    assert ("TestOuter.TestInner.test_price", "DELETED") in signals(inspect_diff(before, after))


def test_fixture_edit_marks_file(inspect_diff):
    before = "def fixture():\n    return 1\n\n" + TEST
    assert (None, "MODIFIED") in signals(
        inspect_diff(before, before.replace("return 1", "return 2"))
    )


def test_comments_and_strings_are_not_markers(inspect_diff):
    after = '# @pytest.mark.skip assert\nnote = "pytest.mark.xfail"\n' + TEST
    assert signals(inspect_diff(TEST, after)) == {(None, "MODIFIED")}


def test_new_file(inspect_diff):
    assert signals(inspect_diff(None, TEST)) == {(None, "MODIFIED")}


def test_clean_diff(inspect_diff):
    assert inspect_diff(TEST, TEST) == []


def test_production_only_diff(inspect_diff):
    assert inspect_diff("x = 1\n", "x = 2\n", path="src/shop.py") == []


def test_suffix_filename_and_literal_git_path(inspect_diff):
    result = signals(inspect_diff(TEST, None, path="tests/[price] shop_test.py"))
    assert (None, "DELETED") in result


def test_committed_source_used(inspect_diff):
    result = signals(inspect_diff(TEST, TEST.replace("    assert 2 == 2\n", ""), dirty=TEST))
    assert ("test_example", "ASSERTION_REMOVED") in result


def test_invalid_source_is_error(inspect_diff):
    with pytest.raises(TamperError, match="Cannot parse Python test file"):
        inspect_diff(TEST, "def test_example(:\n")


def test_large_function_with_decorator_outside_diff(inspect_diff):
    before = "import pytest\n@pytest.mark.skip\n" + TEST + "    value = 1\n" * 40
    result = signals(inspect_diff(before, before + "    assert False\n"))
    assert ("test_example", "MODIFIED") in result
    assert ("test_example", "SKIPPED") not in result


@pytest.mark.parametrize(
    ("base_codes", "head_codes", "expected"),
    [
        ([0, 0, 0], [1, 1, 1], "FAIL"),
        ([0, 0, 0], [0, 0, 0], "PASS"),
        ([1, 1, 1], [0, 0, 0], "PASS"),
        ([2, 2, 2], [0, 0, 0], "PASS"),
        ([1, 1, 1], [1, 1, 1], "ERROR"),
        ([0, 1, 0], [1, 1, 1], "ERROR"),
        ([0, 0, 0], [1, 0, 1], "ERROR"),
        ([0, 0, 0], [2, 2, 2], "ERROR"),
        ([0, 0, 0], [6, 6, 6], "ERROR"),
        ([0, 0, 0], [None, None, None], "ERROR"),
        ([None, 0, 0], [1, 1, 1], "ERROR"),
    ],
)
def test_base_tests_on_head_rule(inspect_diff, tmp_path, base_codes, head_codes, expected):
    (tmp_path / "README.md").write_text("Owned fixture repository.\n")
    findings = inspect_diff(TEST, None)
    context = get_context(tmp_path, "HEAD~", "HEAD")
    codes = iter(base_codes + head_codes)
    calls = []

    def runner(image, code_dir, probe_dir, probe_name, timeout):
        assert (probe_dir / probe_name).read_text() == TEST
        assert probe_name == "test_t1.py"
        assert image == "sha256:fake"
        assert (code_dir / "tests/test_example.py").exists() == (code_dir.name == "base")
        calls.append(code_dir.name)
        return next(codes), b"recorded output", b""

    updated, experiments = run_base_tests_on_head(
        tmp_path, context, findings, tmp_path / "evidence", runner=runner, digest="sha256:fake"
    )
    assert calls == ["base"] * 3 + ["head"] * 3
    assert len(experiments) == 1  # File and function findings share one experiment.
    assert experiments[0].hypothesis_id == "T1"
    assert [e.exit_code for e in experiments[0].executions] == base_codes + head_codes
    assert all(f.base_test_on_head_outcome == expected for f in updated)
    assert all(f.base_test_on_head_outcome is None for f in findings)


def test_base_tests_preserve_latin1_literal(inspect_diff, tmp_path):
    (tmp_path / "README.md").write_text("Owned fixture repository.\n")
    source = '# -*- coding: latin-1 -*-\ndef test_example():\n    assert "café" == "café"\n'
    findings = inspect_diff(source.encode("latin-1"), None)
    context = get_context(tmp_path, "HEAD~", "HEAD")
    evidence_dir = tmp_path / "evidence"

    def runner(image, code_dir, probe_dir, probe_name, timeout):
        return 0, b"", b""

    run_base_tests_on_head(
        tmp_path, context, findings, evidence_dir, runner=runner, digest="fake"
    )
    with tokenize.open(evidence_dir / "probes/test_t1.py") as probe:
        assert 'assert "café" == "café"' in probe.read()


def test_base_tests_run_once_per_file_with_distinct_ids(inspect_diff, tmp_path):
    second = tmp_path / "test_second.py"
    second.write_text(TEST)
    findings = inspect_diff(TEST, TEST.replace("2 == 2", "2 == 3"), dirty="invalid dirty source")
    findings.append(TamperFinding(
        test_path="test_second.py", test_name=None, kind="SKIPPED",
        base_test_on_head_outcome=None,
    ))
    findings.append(findings[-1].model_copy(update={"kind": "XFAIL"}))
    findings.append(findings[-1].model_copy(update={"kind": "ASSERTION_REMOVED"}))
    context = get_context(tmp_path, "HEAD~", "HEAD")
    names = []

    def runner(image, code_dir, probe_dir, probe_name, timeout):
        assert (probe_dir / probe_name).read_text() == TEST
        names.append(probe_name)
        return 0, b"", b""

    updated, experiments = run_base_tests_on_head(
        tmp_path, context, findings, tmp_path / "evidence", runner=runner, digest="fake"
    )
    assert names == ["test_t1.py"] * 6 + ["test_t2.py"] * 6
    assert [e.hypothesis_id for e in experiments] == ["T1", "T2"]
    assert all(f.base_test_on_head_outcome == "PASS" for f in updated)


def test_added_test_without_base_version_is_error(inspect_diff, tmp_path):
    findings = inspect_diff(None, TEST)
    context = get_context(tmp_path, "HEAD~", "HEAD")
    updated, experiments = run_base_tests_on_head(tmp_path, context, findings, tmp_path / "evidence")
    assert experiments == []
    assert [f.base_test_on_head_outcome for f in updated] == ["ERROR"]
