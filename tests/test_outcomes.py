"""Offline tests for immutable metric observations and outcome processing."""

import tempfile
import unittest
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vch.database import VchDatabase
from vch.config import RedditSettings
from vch.domain import CollectionStatus, ContentItem, MetricSnapshot, Platform, PredictionRecord, SemanticFeatures
from vch.reddit import RedditMetricCollector
from vch.service import OutcomeService, calculate_growth, evaluation_summary


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def content() -> ContentItem:
    return ContentItem(external_id="one", platform=Platform.REDDIT, title="One", url="https://reddit.com/x", published_at=NOW)


class OutcomesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = VchDatabase(Path(self.directory.name) / "vch.sqlite3")
        self.database.initialize()
        self.content_id = self.database.store_content(content())
        self.baseline = self.database.store_metric_snapshot(MetricSnapshot(content_id=self.content_id, platform=Platform.REDDIT, observed_at=NOW, collector_version="test", reddit_score=0, reddit_comment_count=4))

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_snapshots_are_append_only_and_zero_baseline_has_no_percentage(self) -> None:
        later = self.database.store_metric_snapshot(MetricSnapshot(content_id=self.content_id, platform=Platform.REDDIT, observed_at=NOW + timedelta(hours=24), collector_version="test", reddit_score=9, reddit_comment_count=6))
        growth = calculate_growth(self.baseline, later)
        self.assertEqual(len(self.database.list_metric_snapshots(self.content_id)), 2)
        self.assertEqual(growth["reddit_score"].absolute_change, 9)
        self.assertIsNone(growth["reddit_score"].relative_growth_percent)
        self.assertEqual(growth["reddit_comment_count"].relative_growth_percent, 50)

    def test_early_outcome_is_pending_then_due_outcome_is_observed(self) -> None:
        class Collector:
            def collect(_, content_id: int, baseline: MetricSnapshot) -> MetricSnapshot:
                return MetricSnapshot(content_id=content_id, platform=Platform.REDDIT, observed_at=NOW + timedelta(hours=25), collector_version="fake", reddit_score=10)

        early = OutcomeService(self.database, Collector(), clock=lambda: NOW + timedelta(hours=23)).collect_due(24)
        due = OutcomeService(self.database, Collector(), clock=lambda: NOW + timedelta(hours=25)).collect_due(24)
        self.assertEqual(early.pending, 1)
        self.assertEqual(due.observed, 1)
        self.assertEqual(self.database.list_outcomes()[0].growth["reddit_score"].absolute_change, 10)

    def test_leakage_guard_rejects_pre_target_followup(self) -> None:
        outcome = self.database.get_or_create_outcome(self.content_id, 24, self.baseline)
        early = self.database.store_metric_snapshot(MetricSnapshot(content_id=self.content_id, platform=Platform.REDDIT, observed_at=NOW + timedelta(hours=2), collector_version="test", reddit_score=3))
        with self.assertRaises(ValueError):
            self.database.complete_outcome(outcome.model_copy(update={"follow_up_snapshot_id": early.id, "observed_at": early.observed_at}))

    def test_evaluation_has_no_model_accuracy_claim(self) -> None:
        summary = evaluation_summary(self.database)
        self.assertEqual(summary["initial_metric_snapshots"], 1)
        self.assertIn("not applicable", summary["note"])

    def test_reddit_collector_refreshes_by_stored_id(self) -> None:
        class Client:
            def submission(_, id: str):
                self.assertEqual(id, "one")
                return type("Submission", (), {"score": 12, "num_comments": 5, "upvote_ratio": 0.9, "removed_by_category": None})()
        collector = RedditMetricCollector(self.database, RedditSettings("id", "secret", "agent", ("python",)), lambda _: Client(), lambda: NOW + timedelta(hours=25))
        result = collector.collect(self.content_id, self.baseline)
        self.assertEqual(result.reddit_score, 12)
        self.assertEqual(result.observed_at, NOW + timedelta(hours=25))

    def test_transient_failure_persists_and_retries(self) -> None:
        class Flaky:
            calls = 0
            def collect(self, content_id: int, baseline: MetricSnapshot) -> MetricSnapshot:
                self.calls += 1
                if self.calls == 1:
                    raise OSError("network")
                return MetricSnapshot(content_id=content_id, platform=Platform.REDDIT, observed_at=NOW + timedelta(hours=25), collector_version="fake", reddit_score=1)
        collector = Flaky()
        first = OutcomeService(self.database, collector, clock=lambda: NOW + timedelta(hours=25)).collect_due(24)
        second = OutcomeService(self.database, collector, clock=lambda: NOW + timedelta(hours=26)).collect_due(24)
        self.assertEqual(first.failed, 1)
        self.assertEqual(second.observed, 1)
        self.assertEqual(self.database.list_outcomes()[0].status.value, "observed")

    def test_prediction_rejects_future_or_other_content_features(self) -> None:
        feature = self.database.store_features(self.content_id, SemanticFeatures(hook=1, emotion=1, utility=1, surprise=1, controversy=1), "test", NOW + timedelta(hours=1))
        with self.assertRaises(ValueError):
            self.database.store_prediction(PredictionRecord(content_id=self.content_id, predicted_at=NOW, horizon_hours=24, predictor_version="baseline", interpretation="not trained", feature_record_id=feature.id))

    def test_naive_timestamp_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MetricSnapshot(content_id=self.content_id, platform=Platform.REDDIT, observed_at=datetime(2026, 10, 1), collector_version="test")

    def test_existing_slice_five_schema_is_upgraded_without_data_loss(self) -> None:
        path = Path(self.directory.name) / "legacy.sqlite3"
        connection = sqlite3.connect(path)
        try:
            connection.execute("CREATE TABLE metric_snapshots (id INTEGER PRIMARY KEY, content_id INTEGER, platform TEXT, observed_at TEXT, source_published_at TEXT, reddit_score INTEGER, reddit_comment_count INTEGER, reddit_upvote_ratio REAL, youtube_view_count INTEGER, youtube_like_count INTEGER, youtube_comment_count INTEGER, collector_version TEXT, status TEXT, error_message TEXT)")
            connection.execute("CREATE TABLE outcomes (id INTEGER PRIMARY KEY, content_id INTEGER, horizon_hours INTEGER, target_at TEXT, observed_at TEXT, baseline_snapshot_id INTEGER, follow_up_snapshot_id INTEGER, status TEXT, growth_json TEXT, explanation TEXT)")
            connection.execute("INSERT INTO metric_snapshots (id, content_id, platform, observed_at, collector_version, status) VALUES (1, 1, 'reddit', '2026-10-01T00:00:00+00:00', 'old', 'observed')")
            connection.commit()
        finally:
            connection.close()
        database = VchDatabase(path)
        database.initialize()
        connection = sqlite3.connect(path)
        try:
            self.assertEqual(connection.execute("SELECT role FROM metric_snapshots WHERE id=1").fetchone()[0], "t0")
        finally:
            connection.close()
