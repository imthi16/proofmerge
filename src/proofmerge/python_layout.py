"""Trusted Python import paths and hashed dependency installs.

Paths and lockfiles come from the operator or the base commit. The pull request
head is not a source. Installs use pinned wheels with sha256 hashes. An import
or install failure is an environment result, never a behavioral pass or failure.
"""

import hashlib
import os
import platform
import re
import subprocess
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

from proofmerge.project_config import PythonProject

DEFAULT_CONTAINER_PYTHONPATH = "/work/code/src:/work/code"
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_VERSION_NAMES = frozenset({"python_version", "python_full_version"})
_MARKER = re.compile(
    r"\s+"
    r"|(?P<op>===|==|!=|<=|>=|~=|<|>)"
    r"|(?P<paren>[()])"
    r"|(?P<str>'(?:[^'\\])*'|\"(?:[^\"\\])*\")"
    r"|(?P<id>[A-Za-z_][A-Za-z0-9_]*)"
)
_REQ = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9_.!+]+)")
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")
_PYPI = "https://pypi.org/simple"


class LayoutError(Exception):
    """The trusted Python layout or its lockfile cannot be used."""


class ProbeLayout:
    """Import layout for one probe run. `deps_dir` is None when install is off."""

    def __init__(self, project: PythonProject | None, deps_dir: Path | None) -> None:
        self.project = project
        self.deps_dir = deps_dir


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def import_relatives(project: PythonProject | None) -> list[str] | None:
    """Repository-relative import paths, or None for the historical src/root layout."""
    if project is None or (project.root is None and not project.pythonpath):
        return None
    entries: list[str] = []
    if project.root is not None:
        entries.append(project.root)
    for extra in project.pythonpath:
        if extra not in entries:
            entries.append(extra)
    return entries


def container_pythonpath(project: PythonProject | None, *, deps: bool) -> str:
    rels = import_relatives(project)
    if rels is None:
        path = DEFAULT_CONTAINER_PYTHONPATH
    else:
        path = ":".join("/work/code" if rel == "" else f"/work/code/{rel}" for rel in rels)
    if deps:
        return "/deps:" + path
    return path


def host_pythonpath(
    code_dir: Path, project: PythonProject | None, deps_dir: Path | None
) -> str:
    """Same entries as the container, pointed at an exported tree on this machine."""
    rels = import_relatives(project)
    if rels is None:
        parts = [code_dir / "src", code_dir]
    else:
        parts = [code_dir if rel == "" else code_dir / rel for rel in rels]
    text = [str(part) for part in parts]
    if deps_dir is not None:
        text.insert(0, str(deps_dir))
    return os.pathsep.join(text)


def resolve_inside(code_dir: Path, relative: str) -> Path:
    """Reject absolute paths, `..`, missing directories and any symlink on the way."""
    if code_dir.is_symlink():
        raise LayoutError("refusing a symlink at the exported repository root")
    root = code_dir.resolve()
    current = code_dir
    if relative:
        for part in relative.split("/"):
            if part in ("", ".", ".."):
                raise LayoutError(f"refusing Python import path {relative!r}")
            current = current / part
            if current.is_symlink():
                raise LayoutError(f"refusing symlink in Python import path: {relative}")
            if not current.exists():
                raise LayoutError(
                    f"Python import path does not exist in this revision: {relative}"
                )
    if not current.is_dir():
        raise LayoutError(f"Python import path is not a directory: {relative or '.'}")
    resolved = current.resolve()
    if resolved != root and root not in resolved.parents:
        raise LayoutError(f"Python import path escapes the repository: {relative or '.'}")
    return current


def path_problem(code_dir: Path, project: PythonProject | None) -> str | None:
    rels = import_relatives(project)
    if rels is None:
        return None
    try:
        for rel in rels:
            resolve_inside(code_dir, rel)
    except LayoutError as exc:
        return str(exc)
    return None


def is_environment_failure(exit_code: int | None, stdout: bytes, stderr: bytes) -> bool:
    """True when a non-assertion run died importing a module.

    Exit 0 is a pass and exit 1 is an assertion failure. Neither is an
    environment result, even if the output mentions ImportError.
    """
    if exit_code in (0, 1, None):
        return False
    text = stdout + b"\n" + stderr
    return b"ModuleNotFoundError" in text or b"ImportError" in text


def layout_prompt_note(project: PythonProject | None) -> str:
    """Trusted layout text appended to the Python probe prompt. Empty when unused."""
    if project is None:
        return ""
    rels = import_relatives(project)
    lines: list[str] = []
    if rels is not None:
        shown = ", ".join(rel or "." for rel in rels)
        lines.extend(
            [
                "Trusted Python layout from the base commit, not from the pull request:",
                f"Import paths (repository-relative): {shown}.",
                f"Container PYTHONPATH: {container_pythonpath(project, deps=project.install)}.",
                "Import `<import-path>/pkg/mod.py` as `pkg.mod`, not via directories above that path.",
                "Ignore the default `src/` and repository-root layout described above.",
            ]
        )
    if project.install:
        lines.append(
            "Third-party packages from the base commit's lockfile are installed "
            "before the probe runs and can be imported. Packages are not installed "
            "from the pull request. The probe process still has no network."
        )
    elif rels is not None:
        lines.append(
            "Only pytest and the standard library are installed, plus the repository "
            "tree. A missing third-party import is an environment error, not evidence."
        )
    return "\n".join(lines)


def read_commit_file(repo: Path, sha: str, relpath: str) -> bytes:
    if not _SHA.fullmatch(sha):
        raise LayoutError(f"not a full commit SHA: {sha!r}")
    if not relpath or relpath.startswith("-"):
        raise LayoutError(f"refusing commit path {relpath!r}")
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{sha}:{relpath}"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise LayoutError(f"cannot read {relpath} at {sha}: {detail}")
    return result.stdout


def marker_environment(python_version: str, machine: str | None = None) -> dict[str, str]:
    """Marker values for the Linux runner image, not for this host's interpreter.

    `python_version` is the image's version (default 3.13). `platform_machine`
    follows this machine because a native Docker run uses the same architecture.
    """
    if not re.fullmatch(r"\d+\.\d+", python_version):
        raise LayoutError(f"bad python_version {python_version!r}")
    return {
        "python_version": python_version,
        "python_full_version": f"{python_version}.0",
        "os_name": "posix",
        "sys_platform": "linux",
        "platform_machine": machine or platform.machine(),
        "platform_system": "Linux",
        "platform_python_implementation": "CPython",
        "implementation_name": "cpython",
    }


def eval_marker(marker: str, env: dict[str, str]) -> bool:
    if len(marker) > 500:
        raise LayoutError("marker is too long")
    tokens = _tokenize_marker(marker)
    if not tokens:
        raise LayoutError("empty marker")
    value = _eval_or(tokens, env)
    if tokens:
        raise LayoutError(f"trailing marker tokens: {marker}")
    return value


def _tokenize_marker(marker: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(marker):
        match = _MARKER.match(marker, pos)
        if not match:
            raise LayoutError(f"cannot parse marker: {marker}")
        pos = match.end()
        kind = match.lastgroup
        if kind is None:
            continue
        value = match.group(0)
        if kind == "str":
            value = value[1:-1]
        tokens.append((kind, value))
    return tokens


def _eval_or(tokens: list[tuple[str, str]], env: dict[str, str]) -> bool:
    value = _eval_and(tokens, env)
    while tokens and tokens[0] == ("id", "or"):
        tokens.pop(0)
        right = _eval_and(tokens, env)
        value = value or right
    return value


def _eval_and(tokens: list[tuple[str, str]], env: dict[str, str]) -> bool:
    value = _eval_atom(tokens, env)
    while tokens and tokens[0] == ("id", "and"):
        tokens.pop(0)
        right = _eval_atom(tokens, env)
        value = value and right
    return value


def _eval_atom(tokens: list[tuple[str, str]], env: dict[str, str]) -> bool:
    if not tokens:
        raise LayoutError("marker ended early")
    if tokens[0] == ("paren", "("):
        tokens.pop(0)
        value = _eval_or(tokens, env)
        if not tokens or tokens[0] != ("paren", ")"):
            raise LayoutError("marker is missing ')'")
        tokens.pop(0)
        return value
    return _eval_comparison(tokens, env)


def _eval_comparison(tokens: list[tuple[str, str]], env: dict[str, str]) -> bool:
    left = _pop_primary(tokens)
    op = _pop_op(tokens)
    right = _pop_primary(tokens)
    return _compare(left, op, right, env)


def _pop_primary(tokens: list[tuple[str, str]]) -> tuple[str, str]:
    if not tokens:
        raise LayoutError("marker ended early")
    kind, value = tokens.pop(0)
    if kind not in ("id", "str") or value in {"and", "or", "not", "in"}:
        raise LayoutError(f"expected a name or string in a marker, got {value!r}")
    return kind, value


def _pop_op(tokens: list[tuple[str, str]]) -> str:
    if not tokens:
        raise LayoutError("marker is missing an operator")
    kind, value = tokens.pop(0)
    if kind == "id" and value == "not":
        if not tokens or tokens.pop(0) != ("id", "in"):
            raise LayoutError("expected 'not in' in a marker")
        return "not in"
    if kind == "id" and value == "in":
        return "in"
    if kind == "op":
        return value
    raise LayoutError(f"expected an operator in a marker, got {value!r}")


def _compare(
    left: tuple[str, str], op: str, right: tuple[str, str], env: dict[str, str]
) -> bool:
    if op in {"~=", "==="}:
        raise LayoutError(f"unsupported marker operator {op}")
    (left_kind, left_value), (right_kind, right_value) = left, right
    if left_kind == "id" and right_kind == "str":
        actual = _env_value(left_value, env)
        literal = right_value
        if op == "in":
            return actual in literal
        if op == "not in":
            return actual not in literal
        return _cmp_scalar(left_value, actual, op, literal)
    if left_kind == "str" and right_kind == "id":
        actual = _env_value(right_value, env)
        literal = left_value
        if op == "in":
            return literal in actual
        if op == "not in":
            return literal not in actual
        flipped = {"<": ">", ">": "<", "<=": ">=", ">=": "<="}.get(op, op)
        return _cmp_scalar(right_value, actual, flipped, literal)
    raise LayoutError("a marker comparison must pair a variable with a string")


def _env_value(name: str, env: dict[str, str]) -> str:
    if name not in env:
        raise LayoutError(f"unsupported marker variable {name}")
    return env[name]


def _cmp_scalar(name: str, actual: str, op: str, literal: str) -> bool:
    if name in _VERSION_NAMES:
        return _cmp_version(actual, op, literal)
    if op == "==":
        return actual == literal
    if op == "!=":
        return actual != literal
    raise LayoutError(f"operator {op} is not valid for {name}")


def _version_key(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+(\.\d+)*", value):
        raise LayoutError(f"bad version in marker: {value}")
    return tuple(int(part) for part in value.split("."))


def _cmp_version(actual: str, op: str, literal: str) -> bool:
    if "*" in literal and op not in {"==", "!="}:
        raise LayoutError(f"operator {op} cannot use a wildcard version")
    if op == "==" and literal.endswith(".*"):
        prefix = literal[:-2]
        return actual == prefix or actual.startswith(prefix + ".")
    if op == "!=" and literal.endswith(".*"):
        return not _cmp_version(actual, "==", literal)
    if op not in {"==", "!=", "<", "<=", ">", ">="}:
        raise LayoutError(f"operator {op} is not valid for a version")
    left, right = _version_key(actual), _version_key(literal)
    size = max(len(left), len(right))
    left += (0,) * (size - len(left))
    right += (0,) * (size - len(right))
    return {
        "==": left == right,
        "!=": left != right,
        "<": left < right,
        "<=": left <= right,
        ">": left > right,
        ">=": left >= right,
    }[op]


def validate_index_url(url: str) -> str:
    if any(char in url for char in " \t\r\n"):
        raise LayoutError("index URL contains whitespace")
    if not url.startswith("https://") or "@" in url:
        raise LayoutError("index URL must be https and must not carry credentials")
    host = url.removeprefix("https://").split("/", 1)[0]
    plain = re.fullmatch(r"[A-Za-z0-9.-]+", host)
    with_port = re.fullmatch(r"[A-Za-z0-9.-]+:\d+", host)
    if not host or not (plain or with_port):
        raise LayoutError(f"refusing index URL host {host!r}")
    return url


def requirements_for_base(
    repo: Path, base_sha: str, project: PythonProject
) -> tuple[str, str]:
    """Hashed requirements and index URL from the base commit only."""
    if project.requirements:
        text = read_commit_file(repo, base_sha, project.requirements).decode("utf-8")
        return render_requirements(parse_requirements(text)), _PYPI
    pyproject = None
    if project.pyproject:
        pyproject = read_commit_file(repo, base_sha, project.pyproject).decode("utf-8")
    lock = read_commit_file(repo, base_sha, project.lockfile).decode("utf-8")
    return plan_uv_lock(lock, pyproject, project.python_version)


def parse_requirements(text: str) -> list[tuple[str, str, list[str]]]:
    """Accept only `name==version` lines with one or more sha256 hashes."""
    planned: list[tuple[str, str, list[str]]] = []
    for line in _logical_lines(text):
        if line.startswith("#"):
            continue
        parts = line.split()
        if not parts:
            continue
        if parts[0].startswith("-") or any(
            token.startswith(("http://", "https://", "git+", "file:")) for token in parts
        ):
            raise LayoutError("requirements must be pinned names with hashes, not options or URLs")
        match = _REQ.fullmatch(parts[0])
        if not match:
            raise LayoutError(f"requirement must be name==version: {parts[0]}")
        hashes: list[str] = []
        for token in parts[1:]:
            hashed = _HASH.fullmatch(token)
            if not hashed:
                raise LayoutError(f"only --hash=sha256 is allowed, got {token}")
            if hashed.group(1) not in hashes:
                hashes.append(hashed.group(1))
        if not hashes:
            raise LayoutError(f"{parts[0]} has no sha256 hash")
        planned.append((match.group(1), match.group(2), hashes))
    return planned


def render_requirements(planned: list[tuple[str, str, list[str]]]) -> str:
    lines = ["# Generated by ProofMerge from the base commit. Pinned and hashed."]
    for name, version, hashes in planned:
        chunk = [f"{name}=={version} \\"]
        for index, digest in enumerate(hashes):
            suffix = " \\" if index < len(hashes) - 1 else ""
            chunk.append(f"    --hash=sha256:{digest}{suffix}")
        lines.append("\n".join(chunk))
    return "\n".join(lines) + "\n"


def install_hashed_wheels(requirements: str, wheelhouse: Path, dest: Path) -> None:
    """Unzip wheels whose bytes match the requirement hashes. No network, no setup.py."""
    by_hash: dict[str, Path] = {}
    for wheel in sorted(wheelhouse.glob("*.whl")):
        by_hash[hashlib.sha256(wheel.read_bytes()).hexdigest()] = wheel
    for name, version, hashes in parse_requirements(requirements):
        match = next((by_hash[digest] for digest in hashes if digest in by_hash), None)
        if match is None:
            raise LayoutError(f"no local wheel matched hashes for {name}=={version}")
        _extract_wheel(match, dest)


def lockfiles_differ(repo: Path, base_sha: str, head_sha: str, relpath: str) -> bool:
    try:
        base_bytes = read_commit_file(repo, base_sha, relpath)
    except LayoutError:
        return True
    try:
        head_bytes = read_commit_file(repo, head_sha, relpath)
    except LayoutError:
        return True
    return base_bytes != head_bytes


def scope_notes(
    repo: Path,
    base_sha: str,
    head_sha: str,
    project: PythonProject,
    config_source: str,
) -> list[str]:
    notes: list[str] = []
    rels = import_relatives(project)
    if rels is not None:
        shown = ", ".join(rel or "." for rel in rels)
        notes.append(
            f"Python import paths ({shown}) came from {config_source}, "
            "not from the pull request head."
        )
    if project.install:
        source = project.lockfile or project.requirements
        notes.append(
            "Third-party dependencies were installed from the base commit's "
            f"{source} inside the sandbox. The pull request cannot change that install."
        )
        if source and lockfiles_differ(repo, base_sha, head_sha, source):
            notes.append("Head's dependency file differs from base and was not installed.")
    return notes


def plan_uv_lock(
    text: str, pyproject_text: str | None, python_version: str
) -> tuple[str, str]:
    """Turn a uv.lock into hashed requirements for the Linux runner image.

    Only registry wheels are selected. Path, git and editable packages are refused.
    Sdists are ignored so install does not run setup.py.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise LayoutError(f"uv.lock is not valid TOML: {exc}") from exc
    packages = data.get("package")
    if not isinstance(packages, list) or not packages:
        raise LayoutError("uv.lock has no packages")
    by_name: dict[str, dict] = {}
    for pkg in packages:
        if not isinstance(pkg, dict) or not isinstance(pkg.get("name"), str):
            raise LayoutError("uv.lock package is missing a name")
        key = normalize_name(pkg["name"])
        if key in by_name:
            raise LayoutError(f"uv.lock has two versions of {pkg['name']}")
        by_name[key] = pkg
    roots = [
        pkg
        for pkg in packages
        if isinstance(pkg.get("source"), dict) and ({"virtual", "editable"} & set(pkg["source"]))
    ]
    if len(roots) != 1:
        raise LayoutError(f"uv.lock must contain exactly one project member, found {len(roots)}")
    root = roots[0]
    if pyproject_text is not None:
        _check_pyproject(pyproject_text, root)
    env = marker_environment(python_version)
    selected: dict[str, dict] = {}
    seen: set[tuple[str, tuple[str, ...]]] = set()
    index: str | None = None

    def walk(dep: object) -> None:
        nonlocal index
        if not isinstance(dep, dict) or not isinstance(dep.get("name"), str):
            raise LayoutError("lockfile dependency is malformed")
        marker = dep.get("marker")
        if marker is not None and (not isinstance(marker, str) or not eval_marker(marker, env)):
            return
        extras = dep.get("extra") or []
        if isinstance(extras, str):
            extras = [extras]
        if not isinstance(extras, list) or not all(isinstance(item, str) for item in extras):
            raise LayoutError(f"bad extras for {dep['name']}")
        key = normalize_name(dep["name"])
        edge = (key, tuple(sorted(extras)))
        if edge in seen:
            return
        seen.add(edge)
        pkg = by_name.get(key)
        if pkg is None:
            raise LayoutError(f"uv.lock is missing {dep['name']}")
        source = pkg.get("source") or {}
        if not isinstance(source, dict):
            raise LayoutError(f"bad source for {dep['name']}")
        if "registry" not in source:
            if {"virtual", "editable"} & set(source):
                return
            raise LayoutError(f"refusing non-registry package {dep['name']}")
        registry = source["registry"]
        if not isinstance(registry, str):
            raise LayoutError("registry URL is not a string")
        checked = validate_index_url(registry)
        if index is None:
            index = checked
        elif index != checked:
            raise LayoutError("uv.lock uses more than one package index")
        selected[key] = pkg
        for child in pkg.get("dependencies") or []:
            walk(child)
        optional = pkg.get("optional-dependencies") or {}
        if not isinstance(optional, dict):
            raise LayoutError(f"bad optional-dependencies for {dep['name']}")
        for extra in extras:
            group = optional.get(extra)
            if group is None:
                raise LayoutError(f"{dep['name']} has no extra {extra!r} in the lockfile")
            for child in group:
                walk(child)

    for dep in root.get("dependencies") or []:
        walk(dep)
    planned = [
        (selected[key]["name"], str(selected[key]["version"]), _wheel_hashes(selected[key]))
        for key in sorted(selected)
    ]
    return render_requirements(planned), index or _PYPI


def _check_pyproject(text: str, root: dict) -> None:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise LayoutError(f"pyproject.toml is not valid TOML: {exc}") from exc
    deps = (data.get("project") or {}).get("dependencies") or []
    if not isinstance(deps, list):
        raise LayoutError("pyproject project.dependencies must be a list")
    direct = {
        normalize_name(dep["name"])
        for dep in root.get("dependencies") or []
        if isinstance(dep, dict) and isinstance(dep.get("name"), str)
    }
    for dep in deps:
        if not isinstance(dep, str):
            raise LayoutError("pyproject dependencies must be strings")
        name = re.split(r"[\s\[<>=!~;@]", dep, maxsplit=1)[0]
        if normalize_name(name) not in direct:
            raise LayoutError(f"pyproject dependency {name} is not in the base uv.lock")


def _wheel_hashes(pkg: dict) -> list[str]:
    wheels = pkg.get("wheels") or []
    if not isinstance(wheels, list) or not wheels:
        raise LayoutError(f"{pkg.get('name')} has no wheels; refusing to build an sdist")
    version = pkg.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9_.!+]+", version):
        raise LayoutError(f"{pkg.get('name')} has a bad version")
    hashes: list[str] = []
    for wheel in wheels:
        raw = wheel.get("hash") if isinstance(wheel, dict) else None
        if not isinstance(raw, str) or not raw.startswith("sha256:"):
            raise LayoutError(f"{pkg.get('name')} wheel hash is not sha256")
        digest = raw.removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise LayoutError(f"{pkg.get('name')} wheel hash is malformed")
        if digest not in hashes:
            hashes.append(digest)
    return hashes


def _logical_lines(text: str) -> list[str]:
    logical: list[str] = []
    buf = ""
    for raw in text.splitlines():
        if not buf and (not raw.strip() or raw.strip().startswith("#")):
            continue
        if raw.rstrip().endswith("\\"):
            buf += raw.rstrip()[:-1] + " "
            continue
        logical.append((buf + raw).strip())
        buf = ""
    if buf:
        raise LayoutError("requirements file has a dangling continuation")
    return logical


def _extract_wheel(wheel: Path, dest: Path) -> None:
    root = dest.resolve()
    total = 0
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            name = info.filename
            if "\\" in name or name.startswith("/"):
                raise LayoutError(f"wheel {wheel.name} has an unsafe path")
            parts = [part for part in PurePosixPath(name).parts if part != "."]
            if not parts:
                continue
            if ".." in parts:
                raise LayoutError(f"wheel {wheel.name} has an unsafe path")
            target = root.joinpath(*parts)
            if not str(target).startswith(str(root) + os.sep):
                raise LayoutError(f"wheel {wheel.name} escapes the install directory")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            total += info.file_size
            if info.file_size > 80_000_000 or total > 500_000_000:
                raise LayoutError(f"wheel {wheel.name} is too large to unpack")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open("wb") as out:
                while chunk := src.read(1024 * 1024):
                    out.write(chunk)
