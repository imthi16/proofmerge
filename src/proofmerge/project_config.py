"""Repository-defined test configuration from `.proofmerge.toml`.

The config is read from the BASE commit, so a pull request cannot rewrite how it
is tested. It is validated strictly: anything malformed is a hard error.
"""

import io
import re
import subprocess
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator

_LANGUAGE_RE = re.compile(r"^[a-z0-9+#-]{1,20}$")
_PROBE_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
_ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


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


def load_config(repo: Path, base_sha: str) -> ProjectConfig | None:
    """Read `.proofmerge.toml` from `base_sha`; return None when absent.

    Invalid contents raise `ValueError` with a clear message.
    """
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
    try:
        return ProjectConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f".proofmerge.toml at {base_sha} is invalid: {exc}") from exc
