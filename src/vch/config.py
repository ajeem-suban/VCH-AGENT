"""Environment-backed settings for optional local LLM inference."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """Raised when local runtime settings are absent or invalid."""


@dataclass(frozen=True)
class LocalLlmSettings:
    """Bounded runtime settings for one reusable local GGUF model."""

    model_path: Path
    model_version: str = "phi-3.5-mini-instruct"
    gpu_layers: int = 20
    context_size: int = 2048
    max_tokens: int = 128
    max_input_characters: int = 8000

    def __post_init__(self) -> None:
        if self.gpu_layers < -1:
            raise ConfigurationError("gpu_layers must be -1 or greater.")
        if self.context_size < 256:
            raise ConfigurationError("context_size must be at least 256.")
        if self.max_tokens < 1:
            raise ConfigurationError("max_tokens must be positive.")
        if self.max_input_characters < 1:
            raise ConfigurationError("max_input_characters must be positive.")


@dataclass(frozen=True)
class RedditSettings:
    """Read-only Reddit harvesting configuration."""

    client_id: str
    client_secret: str
    user_agent: str
    subreddits: tuple[str, ...]
    candidate_limit: int = 100
    max_age_hours: int = 24

    def __post_init__(self) -> None:
        if not all((self.client_id, self.client_secret, self.user_agent)):
            raise ConfigurationError("Reddit client ID, secret, and user agent are required.")
        if not self.subreddits:
            raise ConfigurationError("At least one subreddit is required.")
        if self.candidate_limit < 1:
            raise ConfigurationError("candidate_limit must be positive.")
        if self.max_age_hours < 1:
            raise ConfigurationError("max_age_hours must be positive.")


@dataclass(frozen=True)
class YouTubeSettings:
    """Public YouTube RSS harvesting configuration."""

    channel_ids: tuple[str, ...]
    candidate_limit: int = 100
    max_age_hours: int = 24
    request_timeout_seconds: int = 10

    def __post_init__(self) -> None:
        if not self.channel_ids:
            raise ConfigurationError("At least one YouTube channel ID is required.")
        if self.candidate_limit < 1:
            raise ConfigurationError("candidate_limit must be positive.")
        if self.max_age_hours < 1:
            raise ConfigurationError("max_age_hours must be positive.")
        if self.request_timeout_seconds < 1:
            raise ConfigurationError("request_timeout_seconds must be positive.")


@dataclass(frozen=True)
class TranscriptSettings:
    languages: tuple[str, ...] = ("en",)
    max_characters: int = 30000

    def __post_init__(self) -> None:
        if not self.languages:
            raise ConfigurationError("At least one transcript language is required.")
        if self.max_characters < 1:
            raise ConfigurationError("max_characters must be positive.")


def load_local_llm_settings() -> LocalLlmSettings:
    """Read local LLM settings without overwriting existing environment values."""

    load_dotenv(override=False)
    model_path = os.getenv("VCH_LLM_MODEL_PATH")
    if not model_path:
        raise ConfigurationError("VCH_LLM_MODEL_PATH must point to a GGUF model file.")

    return LocalLlmSettings(
        model_path=Path(model_path),
        model_version=os.getenv("VCH_LLM_MODEL_VERSION", "phi-3.5-mini-instruct"),
        gpu_layers=_read_int("VCH_LLM_GPU_LAYERS", 20),
        context_size=_read_int("VCH_LLM_CONTEXT_SIZE", 2048),
        max_tokens=_read_int("VCH_LLM_MAX_TOKENS", 128),
        max_input_characters=_read_int("VCH_LLM_MAX_INPUT_CHARACTERS", 8000),
    )


def load_reddit_settings() -> RedditSettings:
    """Read Reddit credentials and harvesting bounds from the environment."""

    load_dotenv(override=False)
    subreddits = tuple(
        subreddit.strip()
        for subreddit in os.getenv("VCH_REDDIT_SUBREDDITS", "").split(",")
        if subreddit.strip()
    )
    return RedditSettings(
        client_id=os.getenv("REDDIT_CLIENT_ID", ""),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET", ""),
        user_agent=os.getenv("REDDIT_USER_AGENT", ""),
        subreddits=subreddits,
        candidate_limit=_read_int("VCH_REDDIT_CANDIDATE_LIMIT", 100),
        max_age_hours=_read_int("VCH_REDDIT_MAX_AGE_HOURS", 24),
    )


def load_youtube_settings() -> YouTubeSettings:
    """Read public YouTube channel and request bounds from the environment."""

    load_dotenv(override=False)
    channel_ids = tuple(
        channel_id.strip()
        for channel_id in os.getenv("VCH_YOUTUBE_CHANNEL_IDS", "").split(",")
        if channel_id.strip()
    )
    return YouTubeSettings(
        channel_ids=channel_ids,
        candidate_limit=_read_int("VCH_YOUTUBE_CANDIDATE_LIMIT", 100),
        max_age_hours=_read_int("VCH_YOUTUBE_MAX_AGE_HOURS", 24),
        request_timeout_seconds=_read_int("VCH_YOUTUBE_REQUEST_TIMEOUT_SECONDS", 10),
    )


def load_transcript_settings() -> TranscriptSettings:
    """Read bounded transcript retrieval settings from the environment."""

    load_dotenv(override=False)
    languages = tuple(
        language.strip()
        for language in os.getenv("VCH_YOUTUBE_TRANSCRIPT_LANGUAGES", "en").split(",")
        if language.strip()
    )
    return TranscriptSettings(
        languages=languages,
        max_characters=_read_int("VCH_YOUTUBE_TRANSCRIPT_MAX_CHARACTERS", 30000),
    )


def load_database_path() -> Path:
    """Read the local SQLite database path from the environment."""

    load_dotenv(override=False)
    return Path(os.getenv("VCH_DATABASE_PATH", "data/vch.sqlite3"))


def _read_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer.") from error
