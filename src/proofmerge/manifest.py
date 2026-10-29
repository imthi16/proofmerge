"""Hash evidence files to detect changes after capture, without executing them."""

import hashlib
import json
from pathlib import Path, PurePosixPath


class ManifestError(ValueError):
    """Evidence cannot be safely read or recorded."""


def evidence_sha256(evidence_dir: Path, relative_path: str) -> str:
    """Hash a relative evidence file, rejecting traversal and symlinks."""
    path = PurePosixPath(relative_path)
    if path.is_absolute() or ".." in path.parts or not path.parts or "\x00" in relative_path:
        raise ManifestError(f"Unsafe evidence path: {relative_path}.")
    target = evidence_dir
    if target.is_symlink():
        raise ManifestError("Evidence directory is a symlink.")
    # shortcut: assumes a stationary bundle; use descriptor-based reads for concurrent writers.
    for part in path.parts:
        target = target / part
        if target.is_symlink():
            raise ManifestError(f"Symlink is not allowed: {relative_path}.")
    try:
        return hashlib.sha256(target.read_bytes()).hexdigest()
    except (OSError, ValueError) as error:
        raise ManifestError(f"Cannot read evidence file {relative_path}: {error}.") from error


def _scan(evidence_dir: Path) -> tuple[dict[str, Path], list[str]]:
    files = {}
    problems = []

    def visit(directory: Path) -> None:
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(evidence_dir).as_posix()
            if path.is_symlink():
                problems.append(f"Symlink is not allowed: {relative}.")
            elif path.is_dir():
                visit(path)
            elif path.is_file() and relative != "manifest.json":
                files[relative] = path

    if evidence_dir.is_symlink():
        raise ManifestError("Evidence directory is a symlink.")
    visit(evidence_dir)
    return files, problems


def write_manifest(evidence_dir: Path) -> Path:
    """Record every regular file except the manifest itself, with sorted keys."""
    try:
        files, problems = _scan(evidence_dir)
        if problems:
            raise ManifestError(" ".join(problems))
        hashes = {name: evidence_sha256(evidence_dir, name) for name in files}
        manifest = evidence_dir / "manifest.json"
        manifest.write_text(json.dumps({"files": hashes}, indent=2, sort_keys=True) + "\n")
        return manifest
    except OSError as error:
        raise ManifestError(f"Cannot write manifest: {error}.") from error


def verify_manifest(evidence_dir: Path) -> list[str]:
    """Return plain sentences describing missing, changed or unexpected evidence."""
    try:
        files, problems = _scan(evidence_dir)
        manifest = evidence_dir / "manifest.json"
        if manifest.is_symlink():
            return problems
        data = json.loads(manifest.read_text())
        if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
            raise ManifestError("expected an object containing a files object")
        listed = data["files"]
        if any(
            not isinstance(value, str)
            or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
            for value in listed.values()
        ):
            raise ValueError("expected SHA-256 hex strings")
    except (OSError, ValueError) as error:
        return [f"Cannot verify manifest.json: {error}."]

    for name, expected in sorted(listed.items()):
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or not path.parts or "\x00" in name:
            problems.append(f"Unsafe manifest path: {name}.")
        elif name not in files:
            problems.append(f"File missing: {name}.")
        else:
            try:
                if evidence_sha256(evidence_dir, name) != expected:
                    problems.append(f"Hash mismatch: {name}.")
            except ManifestError as error:
                problems.append(str(error))
    for name in sorted(files.keys() - listed.keys()):
        problems.append(f"File present but not listed: {name}.")
    return problems
