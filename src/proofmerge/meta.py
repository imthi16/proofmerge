"""Meta Model API client: the only network path from proofmerge to Meta models.

Request rules follow docs/META_API_NOTES.md: `developer` role, json_schema
response_format, no sampling overrides, no logprobs, `max_completion_tokens`.
"""

import os
from typing import Any, Protocol, TypeVar

import openai
from pydantic import BaseModel, ValidationError

BASE_URL = "https://api.meta.ai/v1"
SPARK_MODEL = "muse-spark-1.3"
WONDER_MODEL = "muse-wonder-1.0"
DEFAULT_MAX_CALLS = 20

T = TypeVar("T", bound=BaseModel)


class MetaError(Exception):
    """A Meta API failure. The message is safe to show: it never contains the key."""


class Completions(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class MetaClient:
    def __init__(
        self, completions: Completions | None = None, *, max_calls: int = DEFAULT_MAX_CALLS
    ) -> None:
        if completions is None:
            key = os.environ.get("MODEL_API_KEY")
            if not key:
                raise MetaError("MODEL_API_KEY is not set; export it in your shell first")
            # SDK retries cover 429/5xx and honour Retry-After.
            completions = openai.OpenAI(
                base_url=BASE_URL, api_key=key, max_retries=2, timeout=180
            ).chat.completions
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
        reasoning_effort: str = "low",
        max_completion_tokens: int = 8192,
    ) -> T:
        """One stateless call whose reply must validate against `schema`."""
        if model.endswith("-contributor"):
            # Contributor mode lets Meta train on prompts, which include repository code.
            raise MetaError(f"refusing contributor model {model}: prompts may be used for training")
        if self.calls >= self.max_calls:
            raise MetaError(f"model call budget exhausted ({self.max_calls} calls)")
        self.calls += 1
        try:
            response = self._completions.create(
                model=model,
                messages=[
                    {"role": "developer", "content": developer},
                    {"role": "user", "content": user},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
                },
                reasoning_effort=reasoning_effort,
                max_completion_tokens=max_completion_tokens,
            )
        # `from None` drops the SDK exception, whose message may echo request details.
        except openai.AuthenticationError:
            raise MetaError("Meta API rejected MODEL_API_KEY (HTTP 401)") from None
        except openai.APIStatusError as error:
            raise MetaError(f"Meta API request failed (HTTP {error.status_code})") from None
        except openai.APIConnectionError:
            raise MetaError("could not reach the Meta API") from None
        self.usage.append(response.usage)
        content = response.choices[0].message.content or ""
        try:
            return schema.model_validate_json(content)
        except ValidationError as error:
            raise MetaError(
                f"model reply did not match {schema.__name__} ({error.error_count()} errors)"
            ) from None
