"""Tests for the first offline VCH feature-extraction slice."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from vch.database import VchDatabase
from vch.domain import ContentItem, Platform, SemanticFeatures
from vch.extractors import FeatureExtractionError, FixtureFeatureExtractor, parse_semantic_features
from vch.service import FeatureExtractionService


def sample_content() -> ContentItem:
    return ContentItem(
        external_id="post-123",
        platform=Platform.REDDIT,
        title="A fresh content item",
        body="Only content available at T0 belongs here.",
        url="https://www.reddit.com/r/example/comments/post123",
        author_or_channel="example_author",
        community_or_channel="example",
        published_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )


class SemanticFeatureTests(unittest.TestCase):
    def test_parser_accepts_complete_in_range_json(self) -> None:
        result = parse_semantic_features(
            '{"hook": 8, "emotion": 7, "utility": 6, "surprise": 5, "controversy": 4}'
        )
        self.assertEqual(result.hook, 8)

    def test_parser_rejects_invalid_json_and_out_of_range_scores(self) -> None:
        with self.assertRaises(FeatureExtractionError):
            parse_semantic_features("not-json")
        with self.assertRaises(FeatureExtractionError):
            parse_semantic_features(
                '{"hook": 11, "emotion": 7, "utility": 6, "surprise": 5, "controversy": 4}'
            )

    def test_model_rejects_non_integer_score(self) -> None:
        with self.assertRaises(ValidationError):
            SemanticFeatures(hook=8.0, emotion=7, utility=6, surprise=5, controversy=4)


class FeaturePersistenceTests(unittest.TestCase):
    def test_service_persists_a_feature_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = VchDatabase(Path(directory) / "vch.sqlite3")
            database.initialize()
            extractor = FixtureFeatureExtractor(
                SemanticFeatures(hook=8, emotion=7, utility=6, surprise=5, controversy=4)
            )

            record = FeatureExtractionService(database, extractor).analyze(sample_content())

        self.assertEqual(record.content_id, 1)
        self.assertEqual(record.extractor_version, "fixture-v1")
        self.assertEqual(record.features.utility, 6)

    def test_batch_persists_valid_features_and_skips_invalid_feature_output(self) -> None:
        class PartiallyFailingExtractor:
            version = "test-v1"

            def extract(self, content: ContentItem) -> SemanticFeatures:
                if content.external_id == "broken":
                    raise FeatureExtractionError("invalid local output")
                return SemanticFeatures(hook=8, emotion=7, utility=6, surprise=5, controversy=4)

        with tempfile.TemporaryDirectory() as directory:
            database = VchDatabase(Path(directory) / "vch.sqlite3")
            database.initialize()
            service = FeatureExtractionService(database, PartiallyFailingExtractor())
            result = service.analyze_batch(
                [sample_content(), sample_content().model_copy(update={"external_id": "broken"})]
            )

        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.failed_count, 1)
