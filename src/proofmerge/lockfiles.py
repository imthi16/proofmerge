"""Hashed requirements from poetry.lock and Pipfile.lock, read as data from the base commit.

Same rules as the uv.lock planner in python_layout: registry packages only, one
package index, sha256 hashes for every package, markers evaluated for the Linux
runner image, and no project code is executed. The output feeds the same
`pip install --require-hashes --only-binary=:all: --no-deps` installer container.
"""

import json
import re
import tomllib

from proofmerge.python_layout import (
    _PYPI,
    LayoutError,
    eval_marker,
    marker_environment,
    normalize_name,
    render_requirements,
    validate_index_url,
)

_NAME = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[([^\]]*)\])?\s*(.*)")
_VERSION = re.compile(r"[A-Za-z0-9_.!+]+")
_SHA256 = re.compile(r"sha256:([0-9a-f]{64})")
# Dependency keys that point at code outside a package index.
_NON_REGISTRY = frozenset({"path", "git", "url", "file", "develop", "editable", "ref"})

Planned = list[tuple[str, str, list[str]]]


def _marker_ok(marker: object, env: dict[str, str], extras: frozenset[str] = frozenset()) -> bool:
    if marker is None:
        return True
    if not isinstance(marker, str):
        raise LayoutError("marker is not a string")
    if "extra" not in marker:
        return eval_marker(marker, env)
    # `extra == "x"` holds when the dependent package was asked for extra x.
    return any(eval_marker(marker, {**env, "extra": extra}) for extra in extras or {""})


def _sha256s(raw: list[object], name: str) -> list[str]:
    hashes: list[str] = []
    for value in raw:
        match = _SHA256.fullmatch(value) if isinstance(value, str) else None
        if not match:
            raise LayoutError(f"{name} has a hash that is not sha256")
        if match.group(1) not in hashes:
            hashes.append(match.group(1))
    return hashes


def _checked_version(name: str, version: object) -> str:
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise LayoutError(f"{name} has a bad version")
    return version


class _Index:
    """The single package index a lockfile may use."""

    def __init__(self) -> None:
        self.url: str | None = None

    def use(self, url: str) -> None:
        checked = validate_index_url(url)
        if self.url is None:
            self.url = checked
        elif self.url != checked:
            raise LayoutError("the lockfile uses more than one package index")


# --- poetry.lock -------------------------------------------------------------


def _requirement(text: str, env: dict[str, str]) -> tuple[str, list[str]] | None:
    """Name and extras of a PEP 508 string, None when its marker excludes this runner."""
    requirement, _, marker = text.partition(";")
    match = _NAME.fullmatch(requirement)
    if not match:
        raise LayoutError(f"cannot read dependency {text!r}")
    if match.group(3).lstrip().startswith("@"):
        raise LayoutError(f"refusing URL dependency {match.group(1)}")
    if marker.strip() and not _marker_ok(marker.strip(), env):
        return None
    extras = [item.strip() for item in (match.group(2) or "").split(",") if item.strip()]
    return match.group(1), extras


def _poetry_constraints(name: str, spec: object) -> list[dict]:
    """A dependency spec as a list of tables: a string is a plain version constraint."""
    items = spec if isinstance(spec, list) else [spec]
    tables = []
    for item in items:
        if isinstance(item, str):
            tables.append({})
        elif isinstance(item, dict):
            if _NON_REGISTRY & set(item):
                raise LayoutError(f"refusing non-registry package {name}")
            tables.append(item)
        else:
            raise LayoutError(f"bad dependency spec for {name}")
    return tables


def _poetry_roots(pyproject_text: str, env: dict[str, str]) -> list[tuple[str, list[str]]]:
    """Main (non-optional, non-group) dependencies, from PEP 621 or Poetry tables."""
    try:
        data = tomllib.loads(pyproject_text)
    except tomllib.TOMLDecodeError as exc:
        raise LayoutError(f"pyproject.toml is not valid TOML: {exc}") from exc
    roots: list[tuple[str, list[str]]] = []
    project_deps = (data.get("project") or {}).get("dependencies") or []
    if not isinstance(project_deps, list) or not all(isinstance(d, str) for d in project_deps):
        raise LayoutError("pyproject project.dependencies must be a list of strings")
    for text in project_deps:
        found = _requirement(text, env)
        if found is not None:
            roots.append(found)
    poetry_deps = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    if not isinstance(poetry_deps, dict):
        raise LayoutError("tool.poetry.dependencies must be a table")
    for name, spec in poetry_deps.items():
        if normalize_name(name) == "python":
            continue
        for table in _poetry_constraints(name, spec):
            platform = table.get("platform")
            if table.get("optional") or (platform is not None and platform != "linux"):
                continue
            if _marker_ok(table.get("markers"), env):
                roots.append((name, list(table.get("extras") or [])))
    return roots


def _package_marker(pkg: dict) -> object:
    marker = pkg.get("markers")
    # Poetry writes one marker per group when they differ; the install is the main group.
    return marker.get("main") if isinstance(marker, dict) else marker


def _extra_members(pkg: dict, extra: str) -> set[str]:
    extras = pkg.get("extras") or {}
    if not isinstance(extras, dict):
        raise LayoutError(f"bad extras for {pkg.get('name')}")
    members = extras.get(extra)
    if members is None:
        raise LayoutError(f"{pkg.get('name')} has no extra {extra!r} in the lockfile")
    names = set()
    for member in members:
        match = _NAME.fullmatch(member) if isinstance(member, str) else None
        if not match:
            raise LayoutError(f"bad extra entry for {pkg.get('name')}")
        names.add(normalize_name(match.group(1)))
    return names


def _poetry_wheels(pkg: dict) -> list[str]:
    files = pkg.get("files") or []
    if not isinstance(files, list):
        raise LayoutError(f"bad files for {pkg.get('name')}")
    wheels = [
        item.get("hash") for item in files
        if isinstance(item, dict) and str(item.get("file", "")).endswith(".whl")
    ]  # fmt: skip
    if not wheels:
        raise LayoutError(f"{pkg.get('name')} has no wheels; refusing to build an sdist")
    return _sha256s(wheels, str(pkg.get("name")))


def plan_poetry_lock(
    text: str, pyproject_text: str | None, python_version: str
) -> tuple[str, str]:
    """Hashed requirements for the main dependencies a poetry.lock resolves.

    poetry.lock does not name the project's own dependencies, so the roots come from
    the base commit's pyproject.toml. Dev groups and optional dependencies without a
    requested extra are not installed.
    """
    if pyproject_text is None:
        raise LayoutError("poetry.lock needs the base commit's pyproject.toml")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise LayoutError(f"poetry.lock is not valid TOML: {exc}") from exc
    packages = data.get("package") or []
    if not isinstance(packages, list):
        raise LayoutError("poetry.lock has no package list")
    by_name: dict[str, list[dict]] = {}
    for pkg in packages:
        if not isinstance(pkg, dict) or not isinstance(pkg.get("name"), str):
            raise LayoutError("poetry.lock package is missing a name")
        by_name.setdefault(normalize_name(pkg["name"]), []).append(pkg)
    env = marker_environment(python_version)
    selected: dict[str, dict] = {}
    seen: set[tuple[str, tuple[str, ...]]] = set()
    index = _Index()

    def pick(name: str) -> dict | None:
        candidates = by_name.get(normalize_name(name))
        if not candidates:
            raise LayoutError(f"poetry.lock is missing {name}")
        usable = [pkg for pkg in candidates if _marker_ok(_package_marker(pkg), env)]
        if len(candidates) == 1:
            return usable[0] if usable else None
        if len(usable) != 1:
            raise LayoutError(f"poetry.lock has {len(usable)} versions of {name} for this runner")
        return usable[0]

    def walk(name: str, extras: list[str]) -> None:
        if not all(isinstance(extra, str) for extra in extras):
            raise LayoutError(f"bad extras for {name}")
        edge = (normalize_name(name), tuple(sorted(extras)))
        if edge in seen:
            return
        seen.add(edge)
        pkg = pick(name)
        if pkg is None:
            return
        source = pkg.get("source")
        if source is None:
            index.use(_PYPI)
        elif isinstance(source, dict) and source.get("type") == "legacy":
            url = source.get("url")
            if not isinstance(url, str):
                raise LayoutError(f"bad source for {name}")
            index.use(url)
        else:
            raise LayoutError(f"refusing non-registry package {name}")
        selected[normalize_name(name)] = pkg
        wanted = {extra: _extra_members(pkg, extra) for extra in extras}
        deps = pkg.get("dependencies") or {}
        if not isinstance(deps, dict):
            raise LayoutError(f"bad dependencies for {name}")
        for child, spec in deps.items():
            for table in _poetry_constraints(child, spec):
                if table.get("optional"):
                    active = frozenset(e for e, members in wanted.items()
                                       if normalize_name(child) in members)
                    if not active or not _marker_ok(table.get("markers"), env, active):
                        continue
                elif not _marker_ok(table.get("markers"), env, frozenset(extras)):
                    continue
                walk(child, list(table.get("extras") or []))

    for name, extras in _poetry_roots(pyproject_text, env):
        walk(name, extras)
    planned: Planned = [
        (pkg["name"], _checked_version(pkg["name"], pkg.get("version")), _poetry_wheels(pkg))
        for _, pkg in sorted(selected.items())
    ]
    return render_requirements(planned), index.url or _PYPI


# --- Pipfile.lock ------------------------------------------------------------


def plan_pipfile_lock(text: str, python_version: str) -> tuple[str, str]:
    """Hashed requirements for a Pipfile.lock's `default` packages (not `develop`).

    Pipfile.lock is already fully resolved, so no graph walk is needed. Its hashes do
    not say which files are wheels; `--only-binary=:all:` still refuses an sdist, so an
    sdist-only package fails the install (REVIEW) instead of running setup.py.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LayoutError(f"Pipfile.lock is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("default", {}), dict):
        raise LayoutError("Pipfile.lock has no default packages")
    sources = ((data.get("_meta") or {}).get("sources")) or []
    if not isinstance(sources, list):
        raise LayoutError("Pipfile.lock _meta.sources must be a list")
    urls = {}
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("url"), str):
            raise LayoutError("Pipfile.lock source is missing a url")
        urls[source.get("name")] = source["url"]
    first = sources[0]["url"] if sources else _PYPI
    env = marker_environment(python_version)
    index = _Index()
    planned: Planned = []
    for name, entry in sorted(data.get("default", {}).items()):
        if not isinstance(entry, dict):
            raise LayoutError(f"bad Pipfile.lock entry for {name}")
        # Pipenv leaves platform-excluded packages unpinned and unhashed, so check first.
        if not _marker_ok(entry.get("markers"), env):
            continue
        if _NON_REGISTRY & set(entry):
            raise LayoutError(f"refusing non-registry package {name}")
        version = entry.get("version")
        if not isinstance(version, str) or not version.startswith("=="):
            raise LayoutError(f"{name} is not pinned with == in Pipfile.lock")
        hashes = _sha256s(entry.get("hashes") or [], name)
        if not hashes:
            raise LayoutError(f"{name} has no sha256 hash")
        source_name = entry.get("index")
        if source_name is not None and source_name not in urls:
            raise LayoutError(f"{name} names an unknown index {source_name!r}")
        index.use(urls[source_name] if source_name is not None else first)
        planned.append((name, _checked_version(name, version[2:]), hashes))
    return render_requirements(planned), index.url or _PYPI
