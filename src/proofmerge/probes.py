"""Spark probes: one small pytest test per hypothesis, made to pass on base first.

Spark sees base results only. After a failed base run it gets that run's output and
may revise the probe, up to MAX_BASE_RUNS runs. Head runs only once the probe passes
on base, so a probe can never be fitted to head's behavior.

For repositories with `.proofmerge.toml`, the generic prompt is used and the
Python-only `private_names` check is skipped: other languages may not expose a
public/private boundary we can inspect statically.
"""

import ast
import json
import shlex
import subprocess
from importlib import resources
from pathlib import Path

from pydantic import BaseModel

from proofmerge.context import GitContext
from proofmerge.evidence import Experiment
from proofmerge.hypotheses import Hypothesis
from proofmerge.intent import IntentContract
from proofmerge.meta import SPARK_MODEL, MetaClient, MetaError
from proofmerge.project_config import ProjectConfig
from proofmerge.sandbox import (
    Runner,
    SandboxError,
    config_docker_runner,
    docker_runner,
    image_digest,
    run_config_experiment,
    run_experiment,
)

MAX_BASE_RUNS = 3
MAX_SOURCE_CHARS = 60_000  # base source sent to Spark, over all changed files
MAX_PROBE_CHARS = 20_000
MAX_FEEDBACK_CHARS = 4_000  # tail of each base log sent back to Spark
PROMPT = (resources.files("proofmerge") / "prompts" / "probe.md").read_text()
GENERIC_PROMPT = (resources.files("proofmerge") / "prompts" / "probe_generic.md").read_text()


class ProposedProbe(BaseModel):
    test_source: str


class Probe(BaseModel):
    hypothesis_id: str
    test_source: str  # the last draft; empty when Spark gave up
    drafts: int  # Spark replies used, at most MAX_BASE_RUNS
    passed_on_base: bool


def base_sources(repo: Path, context: GitContext) -> str:
    """The base version of every changed file that exists on base, within MAX_SOURCE_CHARS."""
    parts: list[str] = []
    size = 0
    for path in context.changed_files:
        # The argument starts with the SHA, so a path can never be read as an option.
        shown = subprocess.run(
            ["git", "-C", str(repo), "show", f"{context.base_sha}:{path}"],
            capture_output=True,
            check=False,
        )
        if shown.returncode != 0:
            continue  # added by the patch
        text = shown.stdout.decode(errors="replace")
        if size + len(text) > MAX_SOURCE_CHARS:
            parts.append(f"--- {path} (omitted: over the size limit)")
            continue
        size += len(text)
        parts.append(f"--- {path}\n{text}")
    return "\n".join(parts)


def _private(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


REFLECTION = {"__dict__", "vars", "globals", "locals"}  # name lookups by string


def private_names(source: str) -> list[str]:
    """Private names a probe reaches into. A probe that edits a module's private data
    (a price table under a cache, say) tests the implementation, not what callers see.

    shortcut: a heuristic against an honest model overreaching, not a sandbox; a
    determined probe can still build a name at runtime. The verdict must not rely on it.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []  # pytest reports it as a collection error, which is ERROR
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if _private(node.attr) or node.attr in REFLECTION:
                found.add(node.attr)
        elif isinstance(node, ast.Name) and node.id in REFLECTION:
            found.add(node.id)
        elif isinstance(node, ast.ImportFrom):
            found.update(alias.name for alias in node.names if _private(alias.name))
            found.update(p for p in (node.module or "").split(".") if _private(p))
        elif isinstance(node, ast.Import):
            found.update(p for alias in node.names for p in alias.name.split(".") if _private(p))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # getattr(m, "_cache"), monkeypatch.setattr(m, "_T", ...), sys.modules["p._impl"]
            found.update(p for p in node.value.split(".") if _private(p))
    return sorted(found)


def _tail(path: Path) -> str:
    return path.read_bytes()[-MAX_FEEDBACK_CHARS:].decode(errors="replace")


def _probe_prompt(config: ProjectConfig | None) -> str:
    """Return the developer prompt for Spark, with generic placeholders filled."""
    if config is None:
        return PROMPT
    return (
        GENERIC_PROMPT.replace("{language}", config.language)
        .replace("{probe_path}", config.probe_path)
        .replace("{test_command}", shlex.join(config.test_command))
        .replace("{assertion_failure}", config.assertion_failure)
    )


def write_probe(
    repo: Path,
    context: GitContext,
    contract: IntentContract,
    hypothesis: Hypothesis,
    client: MetaClient,
    evidence_dir: Path,
    *,
    sources: str,
    config: ProjectConfig | None = None,
    runner: Runner = docker_runner,
    digest: str,
) -> Probe:
    """Ask Spark for a probe and revise it until it passes once on base."""
    claim = next(c for c in contract.claims if c.id == hypothesis.claim_id)
    user = (
        f"Hypothesis:\n{hypothesis.model_dump_json(indent=2)}\n\n"
        f"Claim it tests:\n{claim.model_dump_json(indent=2)}\n\n"
        f"Base source of the changed files:\n{sources}\n\n"
        f"Unified diff from base {context.base_sha} to head {context.head_sha}:\n"
        f"{context.diff_text}"
    )
    history: list[dict[str, str]] = []
    source = ""
    prompt = _probe_prompt(config)
    for run in range(1, MAX_BASE_RUNS + 1):
        reply = client.structured(
            SPARK_MODEL,
            developer=prompt,
            user=user,
            schema=ProposedProbe,
            history=history,
            reasoning_effort="medium",
        )
        source = reply.test_source
        if not source.strip():
            return Probe(
                hypothesis_id=hypothesis.id, test_source="", drafts=run, passed_on_base=False
            )
        history.append({"role": "assistant", "content": reply.model_dump_json()})
        private = [] if config is not None else private_names(source)
        if len(source) > MAX_PROBE_CHARS:
            problem = f"The probe is over {MAX_PROBE_CHARS} characters; shorten it."
        elif private:
            problem = (
                f"The probe uses private names {private}. Use only public functions and "
                "names; return an empty test_source if the hypothesis needs private state."
            )
        else:
            problem = None
        if problem:
            history.append({"role": "user", "content": problem})
            continue
        draft_dir = evidence_dir / "drafts" / f"{hypothesis.id}-{run}"
        if config is not None:
            experiment = run_config_experiment(
                repo,
                context.base_sha,
                None,
                hypothesis.id,
                source,
                config,
                draft_dir,
                runner=config_docker_runner,
                digest=digest,
                attempts=1,
            )
        else:
            experiment = run_experiment(
                repo,
                context.base_sha,
                None,
                hypothesis.id,
                source,
                draft_dir,
                runner=runner,
                digest=digest,
                attempts=1,
            )
        execution = experiment.executions[0]
        if execution.outcome == "PASS":
            return Probe(
                hypothesis_id=hypothesis.id, test_source=source, drafts=run, passed_on_base=True
            )
        # Base output comes from running repository code: untrusted, like the diff.
        feedback = {
            "outcome": execution.outcome,
            "exit_code": execution.exit_code,
            "stdout_tail": _tail(draft_dir / execution.stdout_path),
            "stderr_tail": _tail(draft_dir / execution.stderr_path),
        }
        history.append(
            {
                "role": "user",
                "content": f"The probe did not pass on base:\n{json.dumps(feedback, indent=2)}",
            }
        )
    return Probe(
        hypothesis_id=hypothesis.id,
        test_source=source,
        drafts=MAX_BASE_RUNS,
        passed_on_base=False,
    )


def probe_hypotheses(
    repo: Path,
    context: GitContext,
    contract: IntentContract,
    hypotheses: list[Hypothesis],
    client: MetaClient,
    evidence_dir: Path,
    *,
    config: ProjectConfig | None = None,
    runner: Runner = docker_runner,
    digest: str | None = None,
) -> tuple[list[Probe], list[Experiment]]:
    """Write a probe per hypothesis; run each one that passed on base on base and head."""
    if digest is None:
        digest = image_digest(config.image) if config is not None else image_digest()
    sources = base_sources(repo, context)
    probes: list[Probe] = []
    experiments: list[Experiment] = []
    for hypothesis in hypotheses:
        try:
            probe = write_probe(
                repo,
                context,
                contract,
                hypothesis,
                client,
                evidence_dir,
                sources=sources,
                config=config,
                runner=runner,
                digest=digest,
            )
            probes.append(probe)
            if probe.passed_on_base:
                if config is not None:
                    experiments.append(
                        run_config_experiment(
                            repo,
                            context.base_sha,
                            context.head_sha,
                            hypothesis.id,
                            probe.test_source,
                            config,
                            evidence_dir,
                            runner=config_docker_runner,
                            digest=digest,
                        )
                    )
                else:
                    experiments.append(
                        run_experiment(
                            repo,
                            context.base_sha,
                            context.head_sha,
                            hypothesis.id,
                            probe.test_source,
                            evidence_dir,
                            runner=runner,
                            digest=digest,
                        )
                    )
        except (MetaError, SandboxError, OSError, ValueError) as error:
            # ponytail: partials ride on the exception; a result type if more callers need them
            error.completed = {"probes": probes, "experiments": experiments}
            raise
    return probes, experiments
