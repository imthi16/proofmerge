import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel

from proofmerge import __version__
from proofmerge.context import ContextError, get_context
from proofmerge.hypotheses import generate_hypotheses
from proofmerge.intent import extract_intent
from proofmerge.meta import SPARK_MODEL, MetaClient, MetaError

app = typer.Typer()


class SmokeReply(BaseModel):
    ok: bool
    message: str


@app.callback()
def main() -> None:
    """ProofMerge command-line interface."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(f"proofmerge {__version__}")


@app.command()
def smoke() -> None:
    """Make ONE real Muse Spark call to check API access (uses MODEL_API_KEY)."""
    try:
        client = MetaClient(max_calls=1)
        reply = client.structured(
            SPARK_MODEL,
            developer="You are a connectivity check. Reply only with JSON matching the schema.",
            user='Set ok to true and message to "proofmerge smoke test".',
            schema=SmokeReply,
        )
    except MetaError as error:
        typer.echo(f"smoke failed: {error}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"{SPARK_MODEL}: ok={reply.ok} message={reply.message!r}")
    usage = client.usage[-1]
    if usage is not None:
        typer.echo(f"tokens: prompt={usage.prompt_tokens} completion={usage.completion_tokens}")


@app.command()
def hypotheses(
    repo: Path,
    base: Annotated[str, typer.Option(help="Base ref, e.g. main")],
    head: Annotated[str, typer.Option(help="Head ref, e.g. candidate/tenant-cache-regression")],
    intent_file: Annotated[
        Path | None, typer.Option(help="File holding the PR description")
    ] = None,
) -> None:
    """Print intent claims and Spark hypotheses for base..head (up to 2 real calls)."""
    try:
        context = get_context(repo, base, head)
        intent = intent_file.read_text() if intent_file else None
        client = MetaClient(max_calls=2)
        contract = extract_intent(intent, client)
        found = generate_hypotheses(context, contract, client)
    except (ContextError, MetaError, ValueError, OSError) as error:
        typer.echo(f"hypotheses failed: {error}", err=True)
        raise typer.Exit(1) from None
    report = {
        "base_sha": context.base_sha,
        "head_sha": context.head_sha,
        "diff_sha256": context.diff_sha256,
        "intent": contract.model_dump(),
        "hypotheses": found.model_dump(),
    }
    typer.echo(json.dumps(report, indent=2))
    usage = [u for u in client.usage if u is not None]
    prompt = sum(u.prompt_tokens for u in usage)
    completion = sum(u.completion_tokens for u in usage)
    typer.echo(f"calls={client.calls} tokens: prompt={prompt} completion={completion}", err=True)
