"""Read-only Reddit candidate harvesting and VCH content normalization."""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from pydantic import ValidationError

from .config import RedditSettings
from .domain import ContentItem, Platform

logger = logging.getLogger(__name__)


class RedditHarvestError(RuntimeError):
    """Raised when Reddit cannot provide a candidate listing."""


class RedditClient(Protocol):
    """Small PRAW surface required for read-only harvesting."""

    def subreddit(self, display_name: str) -> Any:
        """Return a PRAW subreddit object."""


RedditClientFactory = Callable[[RedditSettings], RedditClient]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class RedditHarvestResult:
    """Candidates retained from one listing and records skipped safely."""

    candidates: list[ContentItem]
    skipped_count: int


class RedditHarvester:
    """Fetch a broad, fresh Reddit pool without selecting future winners."""

    def __init__(
        self,
        settings: RedditSettings,
        client_factory: RedditClientFactory | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or _create_praw_client
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._client: RedditClient | None = None

    def harvest(self) -> RedditHarvestResult:
        """Collect and normalize recent submissions, skipping bad records only."""

        cutoff = self._clock() - timedelta(hours=self._settings.max_age_hours)
        try:
            submissions = self._get_client().subreddit("+".join(self._settings.subreddits)).new(
                limit=self._settings.candidate_limit
            )
            return self._normalize_listing(submissions, cutoff)
        except RedditHarvestError:
            raise
        except Exception as error:
            raise RedditHarvestError("Reddit candidate harvest failed.") from error

    def _normalize_listing(
        self,
        submissions: Iterable[Any],
        cutoff: datetime,
    ) -> RedditHarvestResult:
        candidates: list[ContentItem] = []
        skipped_count = 0
        try:
            for submission in submissions:
                try:
                    content = _normalize_submission(submission)
                    if content.published_at < cutoff:
                        continue
                    candidates.append(content)
                except (AttributeError, TypeError, ValueError, ValidationError) as error:
                    skipped_count += 1
                    logger.warning("Skipping invalid Reddit submission: %s", error)
        except Exception as error:
            raise RedditHarvestError("Reddit listing could not be read completely.") from error
        return RedditHarvestResult(candidates=candidates, skipped_count=skipped_count)

    def _get_client(self) -> RedditClient:
        if self._client is None:
            self._client = self._client_factory(self._settings)
        return self._client


def _normalize_submission(submission: Any) -> ContentItem:
    permalink = _required_text(submission, "permalink")
    if not permalink.startswith("/"):
        raise ValueError("Reddit permalink must be a relative path.")
    return ContentItem(
        external_id=_required_text(submission, "id"),
        platform=Platform.REDDIT,
        title=_required_text(submission, "title"),
        body=_optional_text(submission, "selftext") or "",
        url=f"https://www.reddit.com{permalink}",
        author_or_channel=_optional_text(submission, "author"),
        community_or_channel=_optional_nested_text(submission, "subreddit", "display_name"),
        published_at=datetime.fromtimestamp(float(getattr(submission, "created_utc")), tz=timezone.utc),
        metadata={
            "score": _optional_number(submission, "score"),
            "num_comments": _optional_number(submission, "num_comments"),
            "upvote_ratio": _optional_number(submission, "upvote_ratio"),
            "is_self": bool(getattr(submission, "is_self", False)),
            "is_original_content": bool(getattr(submission, "is_original_content", False)),
            "is_video": bool(getattr(submission, "is_video", False)),
            "is_nsfw": bool(getattr(submission, "over_18", False)),
            "is_spoiler": bool(getattr(submission, "spoiler", False)),
            "is_stickied": bool(getattr(submission, "stickied", False)),
            "link_flair_text": _optional_text(submission, "link_flair_text"),
            "linked_url": _optional_text(submission, "url"),
        },
    )


def _required_text(value: Any, attribute: str) -> str:
    text = _optional_text(value, attribute)
    if not text:
        raise ValueError(f"Reddit submission is missing {attribute}.")
    return text


def _optional_text(value: Any, attribute: str) -> str | None:
    result = getattr(value, attribute, None)
    if result is None:
        return None
    text = str(result).strip()
    return text or None


def _optional_nested_text(value: Any, attribute: str, nested_attribute: str) -> str | None:
    nested_value = getattr(value, attribute, None)
    return _optional_text(nested_value, nested_attribute) if nested_value else None


def _optional_number(value: Any, attribute: str) -> int | float | None:
    result = getattr(value, attribute, None)
    if result is None:
        return None
    if isinstance(result, bool) or not isinstance(result, int | float):
        raise ValueError(f"Reddit submission has invalid {attribute}.")
    return result


def _create_praw_client(settings: RedditSettings) -> RedditClient:
    try:
        import praw
    except ImportError as error:
        raise RedditHarvestError("PRAW is not installed. Install the reddit project extra.") from error
    return praw.Reddit(
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        user_agent=settings.user_agent,
    )
