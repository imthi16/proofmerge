"""Post a run's verdict and its suggested fixes to the pull request, as one GitHub review.

Inline mode puts each fix on the lines it changes as a GitHub suggestion block (the author
can commit it from the page); a change outside the pull request's diff, which GitHub cannot
anchor, goes in the review body. Overall mode puts everything in the body. The review is a
COMMENT: it never approves or requests changes. Approving is its own command, for a PASS run only.
Nothing posts without an explicit command.
"""

import difflib
import json
import re
import subprocess
from collections.abc import Callable
from typing import Any

from proofmerge.evidence import Proof
from proofmerge.fixes import FixReport, FixResult

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
REPO = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")
MARK = "<!-- proofmerge-review -->"
Runner = Callable[..., Any]


class ReviewError(Exception):
    """A failure safe to show in the page."""


def commentable_lines(diff_text: str) -> dict[str, set[int]]:
    """Head-side line numbers GitHub accepts review comments on, per file: added and context lines."""
    lines: dict[str, set[int]] = {}
    path, number = None, 0
    for row in diff_text.splitlines():
        if row.startswith("+++ "):
            path = row[6:] if row.startswith("+++ b/") else None
            continue
        if row.startswith(("--- ", "diff --git")):
            continue
        hunk = HUNK.match(row)
        if hunk:
            number = int(hunk.group(1))
            continue
        if path is None or not row or row[0] not in " +-\\":
            continue
        if row[0] in " +":
            lines.setdefault(path, set()).add(number)
            number += 1
    return lines


def changes(old: str, new: str) -> list[tuple[int, int, list[str]]]:
    """(first, last, replacement lines) per change, as 1-based head line ranges.

    A pure insertion is anchored on the line before it, which the replacement repeats."""
    a, b = old.splitlines(), new.splitlines()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if i1 == i2:  # insertion
            if i1 == 0:
                out.append((1, 1, b[j1:j2] + a[:1]))
            else:
                out.append((i1, i1, a[i1 - 1 : i1] + b[j1:j2]))
        else:
            out.append((i1 + 1, i2, b[j1:j2]))
    return out


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _quiet(text: str) -> str:
    # Model text must not ping people or teams on GitHub.
    return text.replace("@", "@\u200b")


def _status(fix: FixResult) -> str:
    if fix.verified:
        return (
            f"**Fix verified:** with it, the probe passes on base ({fix.base_outcomes}) and on the "
            f"fixed head ({fix.fixed_outcomes}). Only this probe was re-run."
        )
    return f"**Suggested fix, not verified:** {_quiet(fix.problem or 'it was not checked')}"


def build_review(
    proof: Proof, report: FixReport | None, diff_text: str, heads: dict[str, str], mode: str
) -> dict[str, Any]:
    """The JSON body for POST /repos/{repo}/pulls/{n}/reviews."""
    if mode not in ("inline", "overall"):
        raise ReviewError("Choose inline or overall.")
    anchors = commentable_lines(diff_text)
    reasons = "\n".join(f"- {_quiet(r)}" for r in proof.verdict.reasons)
    body = [MARK, f"### ProofMerge: {proof.verdict.decision}", "", reasons, ""]
    comments = []
    titles = {h.id: h.title for h in proof.hypotheses.hypotheses}
    # Two regressions often share one fix; post it once, naming both.
    groups: dict[tuple, list[FixResult]] = {}
    for fix in report.fixes if report else []:
        key = tuple((f.path, f.content) for f in fix.files) or (fix.hypothesis_id,)
        groups.setdefault(key, []).append(fix)
    for same in groups.values():
        fix = next((f for f in same if f.verified), same[0])
        named = ", ".join(f.hypothesis_id for f in same)
        title = " / ".join(_quiet(titles.get(f.hypothesis_id, "")) for f in same)
        head = f"**{named}** {title}"
        lines_out: list[str] = []
        for file in fix.files:
            for first, last, new in (
                changes(heads.get(file.path, ""), file.content) if mode == "inline" else []
            ):
                if all(n in anchors.get(file.path, set()) for n in range(first, last + 1)):
                    text = "\n".join(new)
                    fence = _fence(text)
                    comment = {
                        "path": file.path,
                        "line": last,
                        "side": "RIGHT",
                        "body": f"{head}: {_quiet(fix.explanation)}\n\n{_status(fix)}\n\n"
                        f"{fence}suggestion\n{text}\n{fence}",
                    }
                    if first < last:
                        comment |= {"start_line": first, "start_side": "RIGHT"}
                    comments.append(comment)
                else:
                    lines_out.append(file.path)
        placed = mode == "inline" and fix.files and not lines_out
        body += [f"#### {head}", _quiet(fix.explanation), "", _status(fix), ""]
        if not placed and fix.patch:
            fence = _fence(fix.patch)
            note = (
                "Outside the pull request's diff, so not inline:"
                if lines_out
                else "Suggested change:"
            )
            body += [note, f"{fence}diff\n{fix.patch.rstrip()}\n{fence}", ""]
        elif placed:
            body += ["The change is on the lines below as a suggestion you can commit.", ""]
    if report is None or not report.fixes:
        body += ["No fix was suggested for this run.", ""]
    body.append(
        f"<sub>Run `{proof.run_id}`, base `{proof.base_sha[:7]}`, head `{proof.head_sha[:7]}`. "
        "Posted by ProofMerge from the evidence bundle; the verdict comes from the probes, not the model.</sub>"
    )
    review: dict[str, Any] = {
        "commit_id": proof.head_sha,
        "event": "COMMENT",
        "body": "\n".join(body),
    }
    if comments:
        review["comments"] = comments
    return review


def build_approval(proof: Proof) -> dict[str, Any]:
    """An APPROVE review on the commit the run checked. Only a PASS run may approve."""
    if proof.verdict.decision != "PASS":
        raise ReviewError(f"This run is {proof.verdict.decision}; only a PASS run can approve a pull request.")
    body = [
        "### ProofMerge: PASS",
        "",
        *[f"- {_quiet(reason)}" for reason in proof.verdict.reasons],
        "",
        *[_quiet(limit) for limit in proof.verdict.scope_limitations],
        "",
        (
            f"<sub>Run `{proof.run_id}`, base `{proof.base_sha[:7]}`, head `{proof.head_sha[:7]}`. "
            "Approved by ProofMerge on the maintainer's request, from the evidence bundle.</sub>"
        ),
    ]
    return {"commit_id": proof.head_sha, "event": "APPROVE", "body": "\n".join(body)}


def post_review(
    repo: str, number: int, review: dict[str, Any], runner: Runner = subprocess.run
) -> str:
    """Create the review with gh and return its URL."""
    if not REPO.fullmatch(repo) or not (0 < number < 10**7):
        raise ReviewError("Not a GitHub repository and pull request number.")
    result = runner(
        ["gh", "api", "--method", "POST", f"repos/{repo}/pulls/{number}/reviews", "--input", "-"],
        input=json.dumps(review),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode:
        # gh's message names the API error (for example a line outside the diff); it carries no token.
        raise ReviewError(
            "GitHub refused the review: " + (result.stderr or result.stdout or "").strip()[:300]
        )
    try:
        return str(json.loads(result.stdout).get("html_url", ""))
    except ValueError:
        return ""
