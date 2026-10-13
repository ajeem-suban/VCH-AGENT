"""Offline evidence-report tests for Slice 6."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vch.database import VchDatabase
from vch.domain import ContentItem, MetricSnapshot, Platform
from vch.trends import TrendCollectionService, compare_metric, trend_report


NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


class TrendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = VchDatabase(Path(self.temp.name) / "test.sqlite3")
        self.db.initialize()
        self.id = self.db.store_content(ContentItem(external_id="post", platform=Platform.REDDIT, title="Post", url="https://reddit.com/post", published_at=NOW - timedelta(days=2), community_or_channel="python"))
        self.first = self.db.store_metric_snapshot(MetricSnapshot(content_id=self.id, platform=Platform.REDDIT, observed_at=NOW - timedelta(hours=24), collector_version="test", reddit_score=10))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_comparison_handles_growth_zero_and_invalid_order(self) -> None:
        later = MetricSnapshot(content_id=self.id, platform=Platform.REDDIT, observed_at=NOW, collector_version="test", reddit_score=15)
        self.assertEqual(compare_metric("reddit_score", self.first, later)["percentage_change"], 50)
        zero = self.first.model_copy(update={"reddit_score": 0})
        self.assertIsNone(compare_metric("reddit_score", zero, later)["percentage_change"])
        self.assertFalse(compare_metric("reddit_score", later, self.first)["available"])

    def test_report_never_invents_history_and_has_topic_evidence(self) -> None:
        report = trend_report(self.db, 48, NOW)
        self.assertEqual(report["posts"][0]["limitations"], ["insufficient_observations"])
        self.db.store_metric_snapshot(MetricSnapshot(content_id=self.id, platform=Platform.REDDIT, observed_at=NOW, collector_version="test", reddit_score=20, role="follow_up"))
        report = trend_report(self.db, 48, NOW)
        self.assertEqual(report["posts"][0]["comparisons"][0]["absolute_change"], 10)
        self.assertEqual(report["topics"][0]["assessment"], "insufficient evidence")

    def test_twelve_hour_cycle_skips_recent_and_appends_due_observation(self) -> None:
        class Collector:
            def collect(_, content_id, baseline):
                return MetricSnapshot(content_id=content_id, platform=Platform.REDDIT, observed_at=NOW, collector_version="fake", reddit_score=12)
        result = TrendCollectionService(self.db, Collector(), lambda: NOW).collect_due()
        self.assertEqual(result.collected, 1)
        self.assertEqual(len(self.db.list_metric_snapshots(self.id)), 2)
        self.assertEqual(TrendCollectionService(self.db, Collector(), lambda: NOW).collect_due().skipped, 1)

    def test_youtube_content_is_skipped_without_reddit_snapshot(self) -> None:
        youtube_id = self.db.store_content(ContentItem(external_id="video", platform=Platform.YOUTUBE, title="Video", url="https://youtube.com/watch?v=video", published_at=NOW - timedelta(days=2)))
        self.db.store_metric_snapshot(MetricSnapshot(content_id=youtube_id, platform=Platform.YOUTUBE, observed_at=NOW - timedelta(hours=24), collector_version="test", youtube_view_count=5))

        class Collector:
            def collect(_, content_id, baseline):
                return MetricSnapshot(content_id=content_id, platform=Platform.REDDIT, observed_at=NOW, collector_version="fake", reddit_score=12)

        result = TrendCollectionService(self.db, Collector(), lambda: NOW).collect_due()
        self.assertEqual(result.skipped, 1)
        self.assertEqual(len(self.db.list_metric_snapshots(youtube_id)), 1)
        self.assertEqual(self.db.list_metric_snapshots(youtube_id)[0].platform, Platform.YOUTUBE)

    def test_failed_snapshot_retries_before_twelve_hours(self) -> None:
        class FlakyCollector:
            calls = 0
            def collect(self, content_id, baseline):
                self.calls += 1
                if self.calls == 1:
                    return MetricSnapshot(content_id=content_id, platform=Platform.REDDIT, observed_at=NOW, collector_version="fake", status="failed", error_message="collection_error")
                return MetricSnapshot(content_id=content_id, platform=Platform.REDDIT, observed_at=NOW + timedelta(hours=1), collector_version="fake", reddit_score=12)

        collector = FlakyCollector()
        first = TrendCollectionService(self.db, collector, lambda: NOW).collect_due()
        second = TrendCollectionService(self.db, collector, lambda: NOW + timedelta(hours=1)).collect_due()
        self.assertEqual(first.failed, 1)
        self.assertEqual(second.collected, 1)
        self.assertEqual(collector.calls, 2)
