"""Fit a pull request's diff into what Spark is shown, and record what it was not shown.

A large pull request used to be refused. Now Spark sees the files most likely to matter (source
first, then tests, never lock, generated or binary files) up to a character budget, and the rest
is named in the prompt and in the verdict's scope. Code that Spark did not see cannot be covered
by a hypothesis, so `verdict.adjudicate` never answers PASS when any is missing.
"""

import re
from pathlib import PurePosixPath

from pydantic import BaseModel

MAX_DIFF_CHARS = 60_000
SECTION = re.compile(r"^diff --git ", re.MULTILINE)
HEADER_PATH = re.compile(r'^diff --git "?a/.*?"? "?b/(.*?)"?$')
GENERATED_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Cargo.lock",
    "go.sum", "Gemfile.lock", "composer.lock", "Pipfile.lock",
}  # fmt: skip
GENERATED_DIRS = {"node_modules", "vendor", "dist", "build", "__snapshots__", ".next", "coverage"}
GENERATED_SUFFIXES = (
    ".min.js", ".min.css", ".map", ".snap", ".lock", ".svg", ".png", ".jpg", ".jpeg", ".gif",
    ".ico", ".woff", ".woff2", ".pdf",
)  # fmt: skip


TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "e2e"}
TEST_NAME = re.compile(r"(^test_|_test\.|\.test\.|\.spec\.|^conftest\.py$)")


def is_test_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return bool(TEST_NAME.search(pure.name)) or any(part in TEST_DIRS for part in pure.parts[:-1])


class DiffView(BaseModel):
    text: str  # the diff the model is shown, with a note naming what it is not shown
    omitted: list[str] = []  # source files not shown, or only partly: no hypothesis covers them
    noted: list[str] = []  # test, lock, generated or binary files not shown: only recorded


def is_generated(path: str) -> bool:
    pure = PurePosixPath(path)
    return (
        pure.name in GENERATED_NAMES
        or path.endswith(GENERATED_SUFFIXES)
        or any(part in GENERATED_DIRS for part in pure.parts[:-1])
    )


def _sections(diff_text: str) -> list[tuple[str, str]]:
    """(path, text) for each file of a unified diff, in order."""
    starts = [m.start() for m in SECTION.finditer(diff_text)]
    if not starts:
        return []
    out = []
    for begin, end in zip(starts, [*starts[1:], len(diff_text)], strict=True):
        text = diff_text[begin:end]
        found = HEADER_PATH.match(text.split("\n", 1)[0])
        out.append((found.group(1) if found else text.split("\n", 1)[0], text))
    return out


def _cut(text: str, limit: int) -> str:
    """The start of one file's diff, ended on a line boundary."""
    head = text[:limit]
    return head[: head.rfind("\n") + 1] if "\n" in head else head


def view_diff(diff_text: str, limit: int = MAX_DIFF_CHARS) -> DiffView:
    if len(diff_text) <= limit:
        return DiffView(text=diff_text)
    sections = _sections(diff_text)
    if not sections:  # not a git diff we can split: show the start of it
        note = "\n[ProofMerge: this diff is over the size limit, so you were not shown everything.]\n"
        return DiffView(text=_cut(diff_text, limit) + note, omitted=["the rest of the diff"])
    generated = [path for path, _ in sections if is_generated(path)]
    code = [(i, path, text) for i, (path, text) in enumerate(sections) if not is_generated(path)]
    # Source before tests, and small files first, so more of the change is covered.
    order = sorted(code, key=lambda c: (is_test_path(c[1]), len(c[2]), c[0]))
    kept: dict[int, str] = {}
    left_out: list[str] = []
    used = 0
    for index, path, text in order:
        if used + len(text) <= limit:
            kept[index] = text
            used += len(text)
        else:
            left_out.append(path)
    if not kept and order:  # every file alone is over budget: show the start of the first one
        index, path, text = order[0]
        kept[index] = _cut(text, limit)
        left_out = [path, *[p for p in left_out if p != path]]  # partly shown still counts
    # A test file Spark did not see is only noted: the tamper guard re-runs the original tests anyway.
    omitted = [path for path in left_out if not is_test_path(path)]
    noted = [path for path in left_out if is_test_path(path)] + generated
    note = ["", "[ProofMerge: this diff is over the size limit, so you were not shown everything."]
    if omitted:
        note.append("Source files not shown (or only partly): " + ", ".join(omitted) + ".")
    if noted:
        note.append("Test, lock, generated or binary files not shown: " + ", ".join(noted) + ".")
    note.append("Write hypotheses only about code you can see.]")
    text = "".join(kept[i] for i in sorted(kept)) + "\n".join(note) + "\n"
    return DiffView(text=text, omitted=omitted, noted=noted)
