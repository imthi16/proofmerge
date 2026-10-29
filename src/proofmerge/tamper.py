"""Static and dynamic signals for changed tests; these findings are not verdicts."""

import ast
import io
import re
import tokenize
from pathlib import Path, PurePosixPath

from proofmerge.context import ContextError, GitContext, _git
from proofmerge.evidence import Experiment, Outcome, TamperFinding, TamperKind
from proofmerge.sandbox import Runner, SandboxError, docker_runner, run_experiment


class TamperError(Exception):
    """A test revision could not be inspected."""


def _name(node: ast.AST, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value, aliases)}.{node.attr}"
    return ""


def _marks(nodes: list[ast.expr], aliases: dict[str, str]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {"SKIPPED": set(), "XFAIL": set()}
    for node in nodes:
        if isinstance(node, (ast.List, ast.Tuple)):
            nested = _marks(node.elts, aliases)
            for kind, values in nested.items():
                result[kind].update(values)
            continue
        target = node.func if isinstance(node, ast.Call) else node
        name = _name(target, aliases)
        # Key by the resolved name plus arguments, so renaming an import alias
        # (`pytest` -> `pt`) does not look like a newly added mark.
        key = name
        if isinstance(node, ast.Call):
            key += ast.dump(ast.Tuple(elts=node.args)) + "".join(map(ast.dump, node.keywords))
        if name in {
            "pytest.mark.skip",
            "pytest.mark.skipif",
            "unittest.skip",
            "unittest.skipIf",
            "unittest.skipUnless",
        }:
            result["SKIPPED"].add(key)
        elif name in {"pytest.mark.xfail", "unittest.expectedFailure"}:
            result["XFAIL"].add(key)
    return result


def _assigned_marks(body: list[ast.stmt]) -> list[ast.expr]:
    values: list[ast.expr] = []
    for node in body:
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "pytestmark"
                for target in node.targets
            )
            or (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "pytestmark"
                and node.value is not None
            )
        ):
            values.append(node.value)
    return values


def _inspect(source: str, path: str) -> tuple[dict, dict]:
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise TamperError(f"Cannot parse Python test file {path!r}") from exc
    aliases: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    marks = {None: _marks(_assigned_marks(tree.body), aliases)}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                functions[node.name] = node
                marks[node.name] = _marks(node.decorator_list, aliases)
        elif isinstance(node, ast.ClassDef):
            _inspect_class(node, "", aliases, functions, marks)
    return functions, marks


def _inspect_class(
    node: ast.ClassDef, prefix: str, aliases: dict, functions: dict, marks: dict
) -> None:
    """Record a collectable test class and, recursively, the test classes nested in it."""
    if not (
        node.name.startswith("Test")
        or any(_name(base, aliases) == "unittest.TestCase" for base in node.bases)
    ):
        return
    qualified = prefix + node.name
    marks[qualified] = _marks(node.decorator_list + _assigned_marks(node.body), aliases)
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith(
            "test"
        ):
            name = f"{qualified}.{child.name}"
            functions[name] = child
            marks[name] = _marks(child.decorator_list, aliases)
        elif isinstance(child, ast.ClassDef):
            _inspect_class(child, qualified + ".", aliases, functions, marks)


def _assert_count(node: ast.AST) -> int:
    # A changed assert is MODIFIED; only a lower count means an assertion was removed.
    return sum(isinstance(child, ast.Assert) for child in ast.walk(node))


def find_tamper(repo_path: str | Path, context: GitContext) -> list[TamperFinding]:
    """Inspect changed Python tests at the context's committed SHAs, without execution.

    Full revisions are necessary: ordinary unified diffs omit enclosing functions,
    decorators and imports. Parse/read failures propagate rather than imply clean tests.
    """
    findings: list[TamperFinding] = []
    for path in context.changed_files:
        filename = PurePosixPath(path).name
        # shortcut: pytest's default Python file names; upgrade for custom collection rules.
        if not (
            filename.endswith(".py")
            and (filename.startswith("test_") or filename.endswith("_test.py"))
        ):
            continue
        sources = []
        for sha in (context.base_sha, context.head_sha):
            exists = _git(
                repo_path,
                "ls-tree",
                "-z",
                sha,
                "--",
                f":(literal){path}",
                error=f"Cannot inspect test revision {sha}",
            )
            if not exists:
                sources.append(None)
                continue
            data = _git(
                repo_path, "show", f"{sha}:{path}", error=f"Cannot read test file {path!r} at {sha}"
            )
            try:
                # Python source may declare a non-UTF-8 encoding.
                sources.append(data.decode(tokenize.detect_encoding(io.BytesIO(data).readline)[0]))
            except (UnicodeError, SyntaxError, LookupError) as exc:
                raise TamperError(f"Cannot decode Python test file {path!r}") from exc
        before, after = sources
        if before == after:
            continue
        old_functions, old_marks = _inspect(before or "", path)
        new_functions, new_marks = _inspect(after or "", path)

        def add(kind: TamperKind, function: str | None = None, test_path: str = path) -> None:
            findings.append(
                TamperFinding(
                    test_path=test_path,
                    test_name=function,
                    kind=kind,
                    base_test_on_head_outcome=None,
                )
            )

        add("DELETED" if after is None else "MODIFIED")
        for name, old in old_functions.items():
            new = new_functions.get(name)
            if new is None:
                add("DELETED", name)
            elif ast.dump(old) != ast.dump(new):
                add("MODIFIED", name)
                if _assert_count(old) > _assert_count(new):
                    add("ASSERTION_REMOVED", name)
        for name, kinds in new_marks.items():
            for kind, values in kinds.items():
                if values - old_marks.get(name, {}).get(kind, set()):
                    add(kind, name)
    return findings


def run_base_tests_on_head(
    repo: Path,
    context: GitContext,
    findings: list[TamperFinding],
    evidence_dir: Path,
    runner: Runner = docker_runner,
    digest: str | None = None,
) -> tuple[list[TamperFinding], list[Experiment]]:
    """Run each affected file's committed base tests on both revisions, without a verdict."""
    experiments: list[Experiment] = []
    outcomes: dict[str, Outcome] = {}

    def stamped() -> list[TamperFinding]:
        return [
            finding.model_copy(update={"base_test_on_head_outcome": outcomes[finding.test_path]})
            if finding.test_path in outcomes
            else finding
            for finding in findings
        ]

    for finding in findings:
        path = finding.test_path
        if path in outcomes:
            continue
        outcomes[path] = "ERROR"
        try:
            if not _git(
                repo, "ls-tree", "-z", context.base_sha, "--", f":(literal){path}",
                error=f"Cannot inspect test revision {context.base_sha}",
            ):
                continue  # A newly added test has no base version to run.
            source = _git(
                repo, "show", f"{context.base_sha}:{path}",
                error=f"Cannot read test file {path!r} at {context.base_sha}",
            )
            encoding, header = tokenize.detect_encoding(io.BytesIO(source).readline)
            lines = source.decode(encoding).split("\n", len(header))
            # Match the UTF-8 probe write, changing only comments Python inspected
            # for an encoding declaration (never text inside a multiline literal).
            for index in range(len(header)):
                lines[index] = re.sub(
                    r"^([ \t\f]*#.*?coding[:=][ \t]*)([-\w.]+)",
                    r"\g<1>utf-8", lines[index], flags=re.ASCII,
                )
            # shortcut: repository conftest.py fixtures are not loaded by the isolated
            # probe; tests needing them read ERROR. Upgrade when fixture support is needed.
            experiment = run_experiment(
                repo, context.base_sha, context.head_sha, f"T{len(experiments) + 1}",
                "\n".join(lines),
                evidence_dir, runner=runner, digest=digest, attempts=3,
            )
            experiments.append(experiment)
            base = [e.outcome for e in experiment.executions if e.revision == "base"]
            head = [e.outcome for e in experiment.executions if e.revision == "head"]
            if head == ["PASS"] * 3:
                outcomes[path] = "PASS"
            elif base == ["PASS"] * 3 and head == ["FAIL"] * 3:
                outcomes[path] = "FAIL"
        except (TamperError, SandboxError, ContextError, OSError, ValueError) as error:
            # ponytail: partials ride on the exception; a result type if more callers need them
            error.completed = {"tamper": stamped(), "experiments": experiments}
            raise
    return stamped(), experiments
