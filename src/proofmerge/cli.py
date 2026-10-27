import typer
from pydantic import BaseModel

from proofmerge import __version__
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
