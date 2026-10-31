"""Repository-defined test configuration from `.proofmerge.toml`.

The config is read from the BASE commit, so a pull request cannot rewrite how it
is tested. It is validated strictly: anything malformed is a hard error.

A `[python]` table does not switch on the generic language runner. It only sets
import paths and an optional hashed install for the Python probe runner. A file
with top-level `language` stays on the generic runner and cannot also set `[python]`.
"""

import io
import re
import subprocess
import tomllib
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

_LANGUAGE_RE = re.compile(r"^[a-z0-9+#-]{1,20}$")
_PROBE_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
_ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_PATH_PART_RE = re.compile(r"[A-Za-z0-9_.-]+")
_SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class ProjectConfig(BaseModel):
    """How a repository asks ProofMerge to build and run its probes."""

    model_config = {"extra": "forbid"}

    language: str = Field(pattern=_LANGUAGE_RE.pattern)
    image: str
    probe_path: str = Field(pattern=_PROBE_PATH_RE.pattern)
    test_command: list[str] = Field(min_length=1)
    assertion_failure: str
    pass_exit_codes: list[int] = [0]
    tmpfs_mb: int = Field(default=256, ge=16, le=2048)
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("probe_path")
    @classmethod
    def _probe_path_relative(cls, value: str) -> str:
        if value.startswith("/") or ".." in value:
            raise ValueError("probe_path must be relative and must not contain '..'")
        return value

    @field_validator("assertion_failure")
    @classmethod
    def _assertion_failure_is_regex(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"assertion_failure is not a valid regex: {exc}") from exc
        return value

    @field_validator("env")
    @classmethod
    def _env_keys_uppercase(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            if not _ENV_KEY_RE.fullmatch(key):
                raise ValueError(
                    f"env key {key!r} must match [A-Z_][A-Z0-9_]* (e.g. HOME, GOCACHE)"
                )
        return value


class PythonProject(BaseModel):
    """Import roots and optional hashed installs for the Python probe runner.

    `root` None and an empty `pythonpath` keep the historical `src` plus repository
    root layout. `install` defaults to false. When it is true, packages come from
    the base commit's `uv.lock` or a hashed requirements file.
    """

    model_config = {"extra": "forbid"}

    root: str | None = None
    pythonpath: list[str] = Field(default_factory=list)
    install: bool = False
    lockfile: str = ""
    pyproject: str = ""
    requirements: str = ""
    python_version: str = Field(default="3.13", pattern=r"^\d+\.\d+$")

    @field_validator("root")
    @classmethod
    def _root_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_repo_path(value, allow_root=True)

    @field_validator("pythonpath")
    @classmethod
    def _pythonpath_entries(cls, value: list[str]) -> list[str]:
        if len(value) > 8:
            raise ValueError("pythonpath has too many entries")
        return [normalize_repo_path(item, allow_root=False) for item in value]

    @field_validator("lockfile", "pyproject", "requirements")
    @classmethod
    def _file_path(cls, value: str) -> str:
        if value == "":
            return ""
        return normalize_repo_path(value, allow_root=False)

    @model_validator(mode="after")
    def _install_sources(self) -> "PythonProject":
        if self.lockfile and self.requirements:
            raise ValueError("set lockfile or requirements, not both")
        if self.install and not self.lockfile and not self.requirements:
            root = self.root or ""
            self.lockfile = f"{root}/uv.lock" if root else "uv.lock"
        if self.lockfile and self.lockfile != "uv.lock" and not self.lockfile.endswith("/uv.lock"):
            raise ValueError("lockfile must be a uv.lock path")
        if self.requirements and not self.requirements.endswith(".txt"):
            raise ValueError("requirements must be a .txt file")
        pyproject_ok = self.pyproject in ("", "pyproject.toml") or self.pyproject.endswith(
            "/pyproject.toml"
        )
        if not pyproject_ok:
            raise ValueError("pyproject must be a pyproject.toml path")
        return self


def normalize_repo_path(value: str, *, allow_root: bool) -> str:
    """Return a repo-relative path with no absolute prefix, `..`, or odd characters."""
    if "\x00" in value or "\\" in value or ":" in value:
        raise ValueError("path contains forbidden characters")
    if value.startswith(("/", "~")):
        raise ValueError("absolute paths are not allowed")
    parts: list[str] = []
    for part in PurePosixPath(value).parts:
        if part == "..":
            raise ValueError("path must not contain '..'")
        if part == ".":
            continue
        if not _PATH_PART_RE.fullmatch(part):
            raise ValueError(f"path component {part!r} has characters that are not allowed")
        if part.startswith("-"):
            raise ValueError("path components must not start with '-'")
        parts.append(part)
    if not parts:
        if allow_root:
            return ""
        raise ValueError("path must not be empty")
    joined = "/".join(parts)
    if len(joined) > 200:
        raise ValueError("path is too long")
    return joined


def parse_config_document(data: dict) -> tuple[ProjectConfig | None, PythonProject | None]:
    """Split a toml document into a generic language config or a Python layout."""
    if not isinstance(data, dict) or not data:
        raise ValueError(".proofmerge.toml is empty")
    if "python" in data and "language" in data:
        raise ValueError(
            ".proofmerge.toml cannot set both language and [python]; "
            "[python] keeps the Python runner and language selects the generic runner"
        )
    if "python" in data:
        extra = set(data) - {"python"}
        if extra:
            raise ValueError(f".proofmerge.toml has unexpected keys: {sorted(extra)}")
        if not isinstance(data["python"], dict):
            raise ValueError("[python] must be a table")
        try:
            return None, PythonProject.model_validate(data["python"])
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
    try:
        return ProjectConfig.model_validate(data), None
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def _read_base_toml(repo: Path, base_sha: str) -> dict | None:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{base_sha}:.proofmerge.toml"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        data = tomllib.load(io.BytesIO(result.stdout))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f".proofmerge.toml at {base_sha} is not valid TOML: {exc}") from exc
    return data


def load_setup(repo: Path, base_sha: str) -> tuple[ProjectConfig | None, PythonProject | None]:
    """Read `.proofmerge.toml` from `base_sha`. Missing file is `(None, None)`."""
    data = _read_base_toml(repo, base_sha)
    if data is None:
        return None, None
    try:
        return parse_config_document(data)
    except ValueError as exc:
        raise ValueError(f".proofmerge.toml at {base_sha} is invalid: {exc}") from exc


def load_config(repo: Path, base_sha: str) -> ProjectConfig | None:
    """Read a generic language config from `base_sha`.

    Returns None when the file is absent or when it is only a `[python]` table.
    Invalid contents raise `ValueError`.
    """
    project, _python = load_setup(repo, base_sha)
    return project


def load_python_project(repo: Path, base_sha: str) -> PythonProject | None:
    """Read `[python]` from `base_sha`. None when the file is absent or has no table."""
    _project, python = load_setup(repo, base_sha)
    return python


def load_operator_config(path: Path) -> tuple[ProjectConfig | None, PythonProject | None]:
    """Read a config file the operator passed on the command line.

    This does not look at the pull request. Callers must already have decided
    the path is trusted.
    """
    if path.is_symlink():
        raise ValueError("operator config must not be a symlink")
    if not path.is_file():
        raise ValueError(f"operator config not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"operator config is not valid TOML: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"cannot read operator config: {exc}") from exc
    try:
        return parse_config_document(data)
    except ValueError as exc:
        raise ValueError(f"operator config is invalid: {exc}") from exc


def assert_operator_config_allowed(repo: Path, base_sha: str, config_file: Path) -> None:
    """Refuse `--config` aimed at a checkout file that is not the base blob.

    A path outside the checkout is the operator's file. The working tree's
    `.proofmerge.toml` is accepted only when its bytes match the base commit,
    so a pull request checkout cannot be passed through by habit.
    """
    if not _SHA_RE.fullmatch(base_sha):
        raise ValueError(f"not a full commit SHA: {base_sha!r}")
    if config_file.is_symlink():
        raise ValueError("operator config must not be a symlink")
    try:
        chosen = config_file.resolve()
        working = (repo / ".proofmerge.toml").resolve()
    except OSError as exc:
        raise ValueError(f"cannot read operator config: {exc}") from exc
    if chosen != working:
        return
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{base_sha}:.proofmerge.toml"],
        capture_output=True,
        check=False,
    )
    try:
        current = config_file.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read operator config: {exc}") from exc
    if result.returncode != 0 or result.stdout != current:
        raise ValueError(
            "refusing --config pointed at the repository's .proofmerge.toml "
            "because it does not match the base commit. Copy the trusted file "
            "outside the checkout, or commit it on the base branch."
        )
