"""Model calls through a coding-agent CLI the user is already signed in to (a plan, not a key).

Prompts carry untrusted repository text, so a CLI is listed here only after a canary
check showed that, with the flags below, it cannot read a file in its working
directory (docs/model-backends.md). Each call runs in a fresh empty directory with a
minimal environment, so ProofMerge's own secrets never reach the CLI.
"""

import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from proofmerge.meta import MetaError

TIMEOUT_SECONDS = 600
# What a signed-in CLI needs to find its binary and login; nothing else is passed on.
_ENV_KEYS = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
             "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "CLAUDE_CONFIG_DIR")


def _claude_argv(model: str, system_file: Path, schema: dict) -> list[str]:
    # Canary-checked with Claude Code 2.1.314: `--tools ""` leaves only structured output.
    argv = [
        "claude", "-p", "--output-format", "json", "--tools", "", "--strict-mcp-config",
        "--setting-sources", "", "--no-session-persistence",
        "--system-prompt-file", str(system_file), "--json-schema", json.dumps(schema),
    ]
    return argv + (["--model", model] if model else [])


def _claude_reply(out: dict) -> tuple[Any, int, int]:
    usage = out.get("usage") or {}
    prompt_tokens = sum(
        usage.get(key, 0)
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )
    content = out.get("structured_output", out.get("result", ""))
    return content, prompt_tokens, usage.get("output_tokens", 0)


BACKENDS: dict[str, tuple[Callable[..., list[str]], Callable[[dict], tuple[Any, int, int]]]] = {
    "claude-cli": (_claude_argv, _claude_reply),
}


def _transcript(messages: list[dict[str, str]]) -> str:
    """The user turn plus any earlier assistant/user turns, as one prompt."""
    return "\n\n".join(f"<{m['role']}>\n{m['content']}\n</{m['role']}>" for m in messages)


class CliCompletions:
    """Stands in for openai's chat.completions, backed by one CLI call per request."""

    def __init__(self, backend: str, runner: Callable[..., Any] = subprocess.run) -> None:
        if backend not in BACKENDS:
            raise MetaError(
                f"unknown PROOFMERGE_BACKEND {backend!r}; choose api or {', '.join(BACKENDS)}"
            )
        self.backend = backend
        self._runner = runner

    def create(self, *, model: str, messages: list[dict[str, str]], response_format: dict,
               **_: Any) -> Any:
        build_argv, read_reply = BACKENDS[self.backend]
        env = {key: os.environ[key] for key in _ENV_KEYS if key in os.environ}
        with tempfile.TemporaryDirectory(prefix="proofmerge-cli-") as workdir:
            system_file = Path(workdir) / "system.txt"
            system_file.write_text(messages[0]["content"], encoding="utf-8")
            argv = build_argv(model, system_file, response_format["json_schema"]["schema"])
            try:
                result = self._runner(
                    argv, input=_transcript(messages[1:]), cwd=workdir, env=env,
                    capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
                )
            except FileNotFoundError:
                raise MetaError(f"{argv[0]} is not installed or not on PATH") from None
            except subprocess.TimeoutExpired:
                raise MetaError(f"{self.backend} timed out after {TIMEOUT_SECONDS}s") from None
        # stderr is not echoed: the CLI is outside ProofMerge's control.
        if result.returncode != 0:
            raise MetaError(f"{self.backend} exited with code {result.returncode}")
        try:
            out = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise MetaError(f"{self.backend} did not print JSON") from None
        if not isinstance(out, dict) or out.get("is_error"):
            raise MetaError(f"{self.backend} reported an error")
        content, prompt_tokens, completion_tokens = read_reply(out)
        text = content if isinstance(content, str) else json.dumps(content)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
            usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
        )
