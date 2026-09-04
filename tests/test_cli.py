"""Offline tests for the VCH command-line interface."""

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from vch.cli import main
from vch.config import ConfigurationError, LocalLlmSettings, RedditSettings, YouTubeSettings
from vch.domain import ContentItem, Platform, SemanticFeatures
from vch.extractors import FixtureFeatureExtractor
from vch.reddit import RedditHarvestResult
from vch.youtube import YouTubeHarvestResult


def candidate() -> ContentItem:
    return ContentItem(
        external_id="post-123",
        platform=Platform.REDDIT,
        title="Fresh post",
        url="https://www.reddit.com/r/example/comments/post123",
        published_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )


class CommandLineTests(unittest.TestCase):
    def test_reddit_harvest_persists_candidates_and_reports_result(self) -> None:
        settings = RedditSettings("client", "secret", "VCH test", ("python",))
        result = RedditHarvestResult(candidates=[candidate()], skipped_count=2)

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "vch.sqlite3"
            output = io.StringIO()
            with (
                patch("vch.cli.load_database_path", return_value=database_path),
                patch("vch.cli.load_reddit_settings", return_value=settings),
                patch("vch.cli.RedditHarvester") as harvester_class,
                redirect_stdout(output),
            ):
                harvester_class.return_value.harvest.return_value = result
                status = main(["harvest", "reddit"])

            self.assertTrue(database_path.exists())

        self.assertEqual(status, 0)
        self.assertIn("Harvested 1 fresh Reddit candidates; skipped 2 invalid candidates.", output.getvalue())

    def test_expected_failure_returns_nonzero_without_traceback(self) -> None:
        output = io.StringIO()
        with patch(
            "vch.cli.load_reddit_settings",
            side_effect=ConfigurationError("REDDIT_CLIENT_ID is required."),
        ), redirect_stderr(output):
            status = main(["harvest", "reddit"])

        self.assertEqual(status, 1)
        self.assertEqual(output.getvalue(), "Harvest failed: REDDIT_CLIENT_ID is required.\n")

    def test_real_missing_reddit_configuration_returns_nonzero(self) -> None:
        output = io.StringIO()
        with patch.dict("os.environ", {}, clear=True), redirect_stderr(output):
            status = main(["harvest", "reddit"])

        self.assertEqual(status, 1)
        self.assertIn("Reddit client ID, secret, and user agent are required.", output.getvalue())

    def test_reddit_scan_persists_feature_snapshots_and_reports_result(self) -> None:
        reddit_settings = RedditSettings("client", "secret", "VCH test", ("python",))
        llm_settings = LocalLlmSettings(model_path=Path("models/test.gguf"))
        harvest = RedditHarvestResult(candidates=[candidate()], skipped_count=1)
        extractor = FixtureFeatureExtractor(
            SemanticFeatures(hook=8, emotion=7, utility=6, surprise=5, controversy=4)
        )

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "vch.sqlite3"
            output = io.StringIO()
            with (
                patch("vch.cli.load_database_path", return_value=database_path),
                patch("vch.cli.load_reddit_settings", return_value=reddit_settings),
                patch("vch.cli.load_local_llm_settings", return_value=llm_settings),
                patch("vch.cli.RedditHarvester") as harvester_class,
                patch("vch.cli.LlamaCppFeatureExtractor", return_value=extractor),
                redirect_stdout(output),
            ):
                harvester_class.return_value.harvest.return_value = harvest
                status = main(["scan", "reddit"])

            self.assertTrue(database_path.exists())

        self.assertEqual(status, 0)
        self.assertIn("Scanned 1 fresh Reddit candidates; stored 1 feature snapshots;", output.getvalue())

    def test_youtube_harvest_persists_candidates_and_reports_feed_failures(self) -> None:
        settings = YouTubeSettings(channel_ids=("UCexample",))
        result = YouTubeHarvestResult([candidate().model_copy(update={"platform": Platform.YOUTUBE})], 1, ("UCbad",))

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "vch.sqlite3"
            output = io.StringIO()
            with (
                patch("vch.cli.load_database_path", return_value=database_path),
                patch("vch.cli.load_youtube_settings", return_value=settings),
                patch("vch.cli.YouTubeHarvester") as harvester_class,
                redirect_stdout(output),
            ):
                harvester_class.return_value.harvest.return_value = result
                status = main(["harvest", "youtube"])

            self.assertTrue(database_path.exists())

        self.assertEqual(status, 0)
        self.assertIn("Harvested 1 fresh YouTube candidates;", output.getvalue())

    def test_youtube_scan_persists_feature_snapshots(self) -> None:
        youtube_settings = YouTubeSettings(channel_ids=("UCexample",))
        llm_settings = LocalLlmSettings(model_path=Path("models/test.gguf"))
        harvest = YouTubeHarvestResult(
            [candidate().model_copy(update={"platform": Platform.YOUTUBE})],
            0,
            (),
        )
        extractor = FixtureFeatureExtractor(
            SemanticFeatures(hook=8, emotion=7, utility=6, surprise=5, controversy=4)
        )

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "vch.sqlite3"
            output = io.StringIO()
            with (
                patch("vch.cli.load_database_path", return_value=database_path),
                patch("vch.cli.load_youtube_settings", return_value=youtube_settings),
                patch("vch.cli.load_local_llm_settings", return_value=llm_settings),
                patch("vch.cli.YouTubeHarvester") as harvester_class,
                patch("vch.cli.LlamaCppFeatureExtractor", return_value=extractor),
                redirect_stdout(output),
            ):
                harvester_class.return_value.harvest.return_value = harvest
                status = main(["scan", "youtube"])

            self.assertTrue(database_path.exists())

        self.assertEqual(status, 0)
        self.assertIn("Scanned 1 fresh YouTube candidates; stored 1 feature snapshots;", output.getvalue())
