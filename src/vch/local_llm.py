"""Lazy, single-instance llama-cpp adapter for semantic feature extraction."""

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from .config import LocalLlmSettings
from .domain import ContentItem, SemanticFeatures
from .extractors import FeatureExtractionError, parse_semantic_features


class LocalLlmUnavailableError(RuntimeError):
    """Raised when the optional local llama-cpp runtime cannot be used."""


class ChatCompletionRuntime(Protocol):
    """Minimal llama-cpp surface needed by this adapter."""

    def create_chat_completion(self, **kwargs: Any) -> Mapping[str, Any]:
        """Generate an OpenAI-compatible chat-completion payload."""


RuntimeFactory = Callable[[LocalLlmSettings], ChatCompletionRuntime]


class LlamaCppFeatureExtractor:
    """Extract validated semantic signals with one reusable local model instance."""

    prompt_version = "semantic-features-v1"

    def __init__(
        self,
        settings: LocalLlmSettings,
        runtime_factory: RuntimeFactory | None = None,
    ) -> None:
        self._settings = settings
        self._runtime_factory = runtime_factory or _create_llama_cpp_runtime
        self._runtime: ChatCompletionRuntime | None = None

    @property
    def version(self) -> str:
        return f"{self._settings.model_version}/{self.prompt_version}"

    def extract(self, content: ContentItem) -> SemanticFeatures:
        """Return schema-validated scores using content available at T0 only."""

        raw_output = self._complete(self._analysis_messages(content))
        try:
            return parse_semantic_features(raw_output)
        except FeatureExtractionError:
            retry_output = self._complete(self._repair_messages(raw_output))
            return parse_semantic_features(retry_output)

    def _complete(self, messages: list[dict[str, str]]) -> str:
        response = self._get_runtime().create_chat_completion(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=self._settings.max_tokens,
        )
        try:
            text = response["choices"][0]["message"]["content"]
        except (IndexError, KeyError, TypeError) as error:
            raise FeatureExtractionError("Local LLM returned no chat-completion text.") from error
        if not isinstance(text, str):
            raise FeatureExtractionError("Local LLM returned non-text feature output.")
        return text

    def _get_runtime(self) -> ChatCompletionRuntime:
        if self._runtime is None:
            self._runtime = self._runtime_factory(self._settings)
        return self._runtime

    def _analysis_messages(self, content: ContentItem) -> list[dict[str, str]]:
        body = content.body[: self._settings.max_input_characters]
        return [
            {
                "role": "system",
                "content": (
                    "Return only a JSON object with integer fields hook, emotion, utility, "
                    "surprise, and controversy. Each value must be from 1 through 10. "
                    "These are candidate semantic signals, not a viral prediction."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Assess only this content available at analysis time.\n"
                    f"Platform: {content.platform.value}\n"
                    f"Title: {content.title}\n"
                    f"Body: {body}"
                ),
            },
        ]

    def _repair_messages(self, invalid_output: str) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "Return only a valid JSON object with integer fields hook, emotion, utility, "
                    "surprise, and controversy. Each value must be from 1 through 10."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Your previous response was invalid. Produce a replacement JSON object only. "
                    f"Previous response: {invalid_output[:1000]}"
                ),
            },
        ]


def _create_llama_cpp_runtime(settings: LocalLlmSettings) -> ChatCompletionRuntime:
    if not settings.model_path.is_file():
        raise LocalLlmUnavailableError(f"GGUF model was not found: {settings.model_path}")
    try:
        from llama_cpp import Llama
    except ImportError as error:
        raise LocalLlmUnavailableError(
            "llama-cpp-python is not installed. Install the local-llm project extra."
        ) from error
    return Llama(
        model_path=str(settings.model_path),
        n_gpu_layers=settings.gpu_layers,
        n_ctx=settings.context_size,
        verbose=False,
    )
