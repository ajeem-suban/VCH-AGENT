"""Offline tests for the lazy llama-cpp semantic-feature adapter."""

import unittest
from datetime import datetime, timezone
from pathlib import Path

from vch.config import ConfigurationError, LocalLlmSettings
from vch.domain import ContentItem, Platform
from vch.extractors import FeatureExtractionError
from vch.local_llm import LlamaCppFeatureExtractor


def sample_content() -> ContentItem:
    return ContentItem(
        external_id="video-123",
        platform=Platform.YOUTUBE,
        title="A fresh video",
        body="A short description that exists at T0.",
        url="https://www.youtube.com/watch?v=video123",
        published_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )


class FakeRuntime:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls: list[dict[str, object]] = []

    def create_chat_completion(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {"choices": [{"message": {"content": self.outputs.pop(0)}}]}


class LocalLlmFeatureExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = LocalLlmSettings(model_path=Path("models/test.gguf"))

    def test_runtime_is_lazy_and_reused(self) -> None:
        runtime = FakeRuntime(
            ['{"hook": 8, "emotion": 7, "utility": 6, "surprise": 5, "controversy": 4}']
        )
        factory_calls = 0

        def factory(settings: LocalLlmSettings) -> FakeRuntime:
            nonlocal factory_calls
            self.assertEqual(settings.context_size, 2048)
            factory_calls += 1
            return runtime

        extractor = LlamaCppFeatureExtractor(self.settings, factory)
        self.assertEqual(factory_calls, 0)

        result = extractor.extract(sample_content())

        self.assertEqual(result.hook, 8)
        self.assertEqual(factory_calls, 1)
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(runtime.calls[0]["response_format"], {"type": "json_object"})

    def test_invalid_output_gets_one_corrective_retry(self) -> None:
        runtime = FakeRuntime(
            [
                "this is not JSON",
                '{"hook": 4, "emotion": 5, "utility": 6, "surprise": 7, "controversy": 8}',
            ]
        )
        extractor = LlamaCppFeatureExtractor(self.settings, lambda settings: runtime)

        result = extractor.extract(sample_content())

        self.assertEqual(result.controversy, 8)
        self.assertEqual(len(runtime.calls), 2)

    def test_second_invalid_output_is_rejected(self) -> None:
        runtime = FakeRuntime(["invalid", "still invalid"])
        extractor = LlamaCppFeatureExtractor(self.settings, lambda settings: runtime)

        with self.assertRaises(FeatureExtractionError):
            extractor.extract(sample_content())

    def test_invalid_runtime_settings_are_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            LocalLlmSettings(model_path=Path("model.gguf"), gpu_layers=-2)
