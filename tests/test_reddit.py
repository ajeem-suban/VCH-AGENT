"""Offline tests for Reddit harvesting and normalized content persistence."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from vch.config import RedditSettings
from vch.database import VchDatabase
from vch.reddit import RedditHarvestError, RedditHarvester


def submission(identifier: str, created_at: datetime, **overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": identifier,
        "title": "Fresh post",
        "selftext": "Text available at collection time.",
        "permalink": f"/r/example/comments/{identifier}/post",
        "author": "example_author",
        "subreddit": SimpleNamespace(display_name="example"),
        "created_utc": created_at.timestamp(),
        "score": 25,
        "num_comments": 4,
        "upvote_ratio": 0.9,
        "is_self": True,
        "is_original_content": False,
        "is_video": False,
        "over_18": False,
        "spoiler": False,
        "stickied": False,
        "link_flair_text": "Discussion",
        "url": "https://www.reddit.com/r/example/comments/post",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeSubreddit:
    def __init__(self, items: list[SimpleNamespace]) -> None:
        self.items = items
        self.requested_limit: int | None = None

    def new(self, limit: int) -> list[SimpleNamespace]:
        self.requested_limit = limit
        return self.items


class FakeRedditClient:
    def __init__(self, items: list[SimpleNamespace]) -> None:
        self.subreddit_client = FakeSubreddit(items)
        self.requested_subreddits: str | None = None

    def subreddit(self, display_name: str) -> FakeSubreddit:
        self.requested_subreddits = display_name
        return self.subreddit_client


class RedditHarvesterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
        self.settings = RedditSettings(
            client_id="client-id",
            client_secret="client-secret",
            user_agent="VCH-Agent test",
            subreddits=("python", "MachineLearning"),
            candidate_limit=100,
            max_age_hours=24,
        )

    def test_harvest_normalizes_fresh_candidates_and_persists_them(self) -> None:
        client = FakeRedditClient([submission("fresh", self.now - timedelta(hours=2))])
        harvester = RedditHarvester(self.settings, lambda settings: client, lambda: self.now)

        result = harvester.harvest()

        self.assertEqual(client.requested_subreddits, "python+MachineLearning")
        self.assertEqual(client.subreddit_client.requested_limit, 100)
        self.assertEqual(len(result.candidates), 1)
        content = result.candidates[0]
        self.assertEqual(content.metadata["score"], 25)
        self.assertFalse(content.metadata["is_nsfw"])

        with tempfile.TemporaryDirectory() as directory:
            database = VchDatabase(Path(directory) / "vch.sqlite3")
            database.initialize()
            content_ids = database.store_content_batch(result.candidates)

        self.assertEqual(content_ids, [1])

    def test_old_and_invalid_submissions_do_not_crash_the_pool(self) -> None:
        client = FakeRedditClient(
            [
                submission("old", self.now - timedelta(hours=25)),
                submission("broken", self.now, title=""),
                submission("fresh", self.now - timedelta(minutes=30)),
            ]
        )
        result = RedditHarvester(self.settings, lambda settings: client, lambda: self.now).harvest()

        self.assertEqual([item.external_id for item in result.candidates], ["fresh"])
        self.assertEqual(result.skipped_count, 1)

    def test_client_failure_is_reported(self) -> None:
        def failing_factory(settings: RedditSettings) -> FakeRedditClient:
            raise OSError("network unavailable")

        harvester = RedditHarvester(self.settings, failing_factory, lambda: self.now)

        with self.assertRaises(RedditHarvestError):
            harvester.harvest()
