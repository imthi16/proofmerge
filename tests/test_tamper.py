"""Inspect owned committed fixtures only; never import or execute their contents."""

import subprocess

import pytest

from proofmerge.context import get_context
from proofmerge.tamper import TamperError, find_tamper


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
