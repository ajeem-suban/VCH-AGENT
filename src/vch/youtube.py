"""Public YouTube Atom-feed harvesting and VCH content normalization."""

import logging
import xml.etree.ElementTree as element_tree
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import ValidationError

from .config import YouTubeSettings
from .domain import ContentItem, Platform

logger = logging.getLogger(__name__)

ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
MEDIA_NAMESPACE = "http://search.yahoo.com/mrss/"
YOUTUBE_NAMESPACE = "http://www.youtube.com/xml/schemas/2015"
NAMESPACES = {"atom": ATOM_NAMESPACE, "media": MEDIA_NAMESPACE, "yt": YOUTUBE_NAMESPACE}


class YouTubeHarvestError(RuntimeError):
    """Raised when no configured YouTube channel feed can be harvested."""


FeedFetcher = Callable[[str, int], str]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class YouTubeHarvestResult:
    """Fresh normalized candidates and feeds that could not be read."""

    candidates: list[ContentItem]
    skipped_count: int
    failed_channel_ids: tuple[str, ...]


class YouTubeHarvester:
    """Collect recent videos from public channel feeds without an API key."""

    def __init__(
        self,
        settings: YouTubeSettings,
        feed_fetcher: FeedFetcher | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._feed_fetcher = feed_fetcher or _fetch_feed
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def harvest(self) -> YouTubeHarvestResult:
        """Merge, filter, and normalize candidates from configured channel feeds."""

        cutoff = self._clock() - timedelta(hours=self._settings.max_age_hours)
        candidates: list[ContentItem] = []
        skipped_count = 0
        failed_channel_ids: list[str] = []

        for channel_id in self._settings.channel_ids:
            try:
                feed_candidates, feed_skipped = _parse_feed(
                    self._feed_fetcher(
                        youtube_feed_url(channel_id),
                        self._settings.request_timeout_seconds,
                    ),
                    channel_id,
                )
            except (OSError, UnicodeDecodeError, element_tree.ParseError, ValueError) as error:
                failed_channel_ids.append(channel_id)
                logger.warning("Skipping YouTube channel %s: %s", channel_id, error)
                continue

            skipped_count += feed_skipped
            candidates.extend(candidate for candidate in feed_candidates if candidate.published_at >= cutoff)

        if len(failed_channel_ids) == len(self._settings.channel_ids):
            raise YouTubeHarvestError("No configured YouTube channel feeds could be harvested.")

        candidates.sort(key=lambda candidate: candidate.published_at, reverse=True)
        return YouTubeHarvestResult(
            candidates=candidates[: self._settings.candidate_limit],
            skipped_count=skipped_count,
            failed_channel_ids=tuple(failed_channel_ids),
        )


def youtube_feed_url(channel_id: str) -> str:
    """Return the documented public uploads-feed URL for a channel ID."""

    return f"https://www.youtube.com/feeds/videos.xml?{urlencode({'channel_id': channel_id})}"


def _parse_feed(feed_xml: str, requested_channel_id: str) -> tuple[list[ContentItem], int]:
    root = element_tree.fromstring(feed_xml)
    if root.tag != f"{{{ATOM_NAMESPACE}}}feed":
        raise ValueError("YouTube response is not an Atom feed.")

    feed_title = _text(root.find("atom:title", NAMESPACES))
    candidates: list[ContentItem] = []
    skipped_count = 0
    for entry in root.findall("atom:entry", NAMESPACES):
        try:
            candidates.append(_normalize_entry(entry, requested_channel_id, feed_title))
        except (TypeError, ValueError, ValidationError) as error:
            skipped_count += 1
            logger.warning("Skipping invalid YouTube feed entry: %s", error)
    return candidates, skipped_count


def _normalize_entry(
    entry: element_tree.Element,
    requested_channel_id: str,
    feed_title: str | None,
) -> ContentItem:
    video_id = _required_text(entry.find("yt:videoId", NAMESPACES), "video ID")
    link = _alternate_link(entry)
    channel_name = _text(entry.find("atom:author/atom:name", NAMESPACES)) or feed_title
    group = entry.find("media:group", NAMESPACES)
    statistics = entry.find("media:group/media:community/media:statistics", NAMESPACES)
    thumbnail = entry.find("media:group/media:thumbnail", NAMESPACES)

    return ContentItem(
        external_id=video_id,
        platform=Platform.YOUTUBE,
        title=_required_text(entry.find("atom:title", NAMESPACES), "title"),
        body=(_text(group.find("media:description", NAMESPACES)) or "") if group is not None else "",
        url=link,
        author_or_channel=channel_name,
        community_or_channel=channel_name,
        published_at=_parse_timestamp(_required_text(entry.find("atom:published", NAMESPACES), "published time")),
        metadata={
            "channel_id": requested_channel_id,
            "channel_title": feed_title,
            "updated_at": _text(entry.find("atom:updated", NAMESPACES)),
            "view_count": _optional_int(statistics.get("views") if statistics is not None else None),
            "thumbnail_url": thumbnail.get("url") if thumbnail is not None else None,
        },
    )


def _alternate_link(entry: element_tree.Element) -> str:
    for link in entry.findall("atom:link", NAMESPACES):
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return link.get("href") or ""
    raise ValueError("YouTube feed entry is missing an alternate link.")


def _parse_timestamp(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("YouTube published time must include a timezone.")
    return timestamp.astimezone(timezone.utc)


def _required_text(element: element_tree.Element | None, field: str) -> str:
    value = _text(element)
    if not value:
        raise ValueError(f"YouTube feed entry is missing {field}.")
    return value


def _text(element: element_tree.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def _optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _fetch_feed(url: str, timeout_seconds: int) -> str:
    request = Request(url, headers={"User-Agent": "VCH-Agent/0.1"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8")
