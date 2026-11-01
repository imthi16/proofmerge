"""Model client: the only path from proofmerge to a model.

The default is the Meta Model API with Muse Spark and Muse Wonder. Request rules
follow docs/META_API_NOTES.md: `developer` role, json_schema response_format, no
sampling overrides, no logprobs, `max_completion_tokens`.

Environment overrides (docs/model-backends.md):
- PROOFMERGE_BACKEND: "api" (default) or a CLI backend from cli_backend.py.
- PROOFMERGE_BASE_URL: another OpenAI-compatible endpoint for the "api" backend.
- PROOFMERGE_SPARK_MODEL / PROOFMERGE_WONDER_MODEL: the model for each role.
- PROOFMERGE_ALLOW_CONTRIBUTOR=1: allow models that may train on prompts.
"""

import os
from collections.abc import Sequence
from typing import Any, Protocol, TypeVar
from urllib.parse import urlsplit, urlunsplit

import openai
from pydantic import BaseModel, ValidationError

BASE_URL = "https://api.meta.ai/v1"
SPARK_MODEL = "muse-spark-1.3"
WONDER_MODEL = "muse-wonder-1.0"
DEFAULT_MAX_CALLS = 20

T = TypeVar("T", bound=BaseModel)


class MetaError(Exception):
    """A Meta API failure. The message is safe to show: it never contains the key.

    `transient` marks failures a later identical call may not hit (HTTP 429, 5xx, no
    connection). Only an orchestrator may retry those, within the same call budget.
    """

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


class Completions(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


def _public_url(url: str) -> str:
    """The endpoint as recorded in evidence: no userinfo, query or fragment."""
    parts = urlsplit(url)
    host = (parts.hostname or "") + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


class MetaClient:
    def __init__(
        self, completions: Completions | None = None, *, max_calls: int = DEFAULT_MAX_CALLS
    ) -> None:
        backend = os.environ.get("PROOFMERGE_BACKEND", "api")
        default_models = completions is not None or backend == "api"
        if completions is not None:
            self.endpoint = "injected"
        elif backend == "api":
            key = os.environ.get("MODEL_API_KEY")
            if not key:
                raise MetaError("MODEL_API_KEY is not set; export it in your shell first")
            base_url = os.environ.get("PROOFMERGE_BASE_URL") or BASE_URL
            # No SDK retries, so every HTTP attempt counts against the call budget.
            # A 429/5xx/connection failure is marked transient; the LangGraph orchestrator
            # retries those within this budget, the legacy one records them as REVIEW.
            completions = openai.OpenAI(
                base_url=base_url, api_key=key, max_retries=0, timeout=180
            ).chat.completions
            self.endpoint = _public_url(base_url)
        else:
            # Imported here: cli_backend imports MetaError from this module.
            from proofmerge.cli_backend import CliCompletions

            completions = CliCompletions(backend)
            self.endpoint = f"cli:{backend}"
        # Callers name a role (SPARK_MODEL or WONDER_MODEL); this maps it to the model used.
        # A CLI backend without an override uses the CLI's own default model ("").
        self.models = {
            role: os.environ.get(variable) or (role if default_models else "")
            for role, variable in (
                (SPARK_MODEL, "PROOFMERGE_SPARK_MODEL"),
                (WONDER_MODEL, "PROOFMERGE_WONDER_MODEL"),
            )
        }
        self.allow_contributor = os.environ.get("PROOFMERGE_ALLOW_CONTRIBUTOR") == "1"
        self._completions = completions
        self.max_calls = max_calls
        self.calls = 0
        self.usage: list[Any] = []

    def structured(
        self,
        model: str,
        *,
        developer: str,
        user: str,
        schema: type[T],
        history: Sequence[dict[str, str]] = (),
        reasoning_effort: str = "low",
        max_completion_tokens: int = 8192,
    ) -> T:
        """One stateless call whose reply must validate against `schema`.

        `history` holds earlier assistant/user turns, sent after the first user message.
        """
        model = self.models.get(model, model)
        if model.endswith("-contributor") and not self.allow_contributor:
            # Contributor mode lets Meta train on prompts, which include repository code.
            raise MetaError(
                f"refusing contributor model {model}: prompts may be used for training;"
                " set PROOFMERGE_ALLOW_CONTRIBUTOR=1 only for code you may share"
            )
        if self.calls >= self.max_calls:
            raise MetaError(f"model call budget exhausted ({self.max_calls} calls)")
        self.calls += 1
        failure = None
        transient = False
        try:
            response = self._completions.create(
                model=model,
                messages=[
                    {"role": "developer", "content": developer},
                    {"role": "user", "content": user},
                    *history,
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
                },
                reasoning_effort=reasoning_effort,
                max_completion_tokens=max_completion_tokens,
            )
        except openai.AuthenticationError:
            failure = "Meta API rejected MODEL_API_KEY (HTTP 401)"
        except openai.APIStatusError as error:
            failure = f"Meta API request failed (HTTP {error.status_code})"
            transient = error.status_code == 429 or error.status_code >= 500
        except openai.APIConnectionError:
            failure = "could not reach the Meta API"
            transient = True
        # Raised outside the except blocks so the SDK exception, whose message may echo
        # the key, is not attached as __context__.
        if failure:
            raise MetaError(failure, transient=transient)
        self.usage.append(response.usage)
        content = response.choices[0].message.content or ""
        try:
            # strict: a reply like {"ok": "false"} must fail, not be coerced to False.
            return schema.model_validate_json(content, strict=True)
        except ValidationError as error:
            errors = error.error_count()
        raise MetaError(f"model reply did not match {schema.__name__} ({errors} errors)")
