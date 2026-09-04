"""Semantic feature-extractor boundary and safe structured-output parsing."""

import json
from typing import Protocol

from pydantic import ValidationError

from .domain import ContentItem, SemanticFeatures


class FeatureExtractionError(ValueError):
    """Raised when an extractor cannot return a valid feature object."""


class SemanticFeatureExtractor(Protocol):
    """Any local inference implementation must return validated signals."""

    version: str

    def extract(self, content: ContentItem) -> SemanticFeatures:
        """Extract semantic signals using only the supplied T0 content."""


def parse_semantic_features(raw_output: str) -> SemanticFeatures:
    """Parse strict JSON returned by a local LLM into validated features."""

    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError as error:
        raise FeatureExtractionError("Feature output is not valid JSON.") from error

    try:
        return SemanticFeatures.model_validate(payload)
    except ValidationError as error:
        raise FeatureExtractionError("Feature output does not match the required schema.") from error


class FixtureFeatureExtractor:
    """Deterministic extractor used by offline development and tests."""

    version = "fixture-v1"

    def __init__(self, features: SemanticFeatures) -> None:
        self._features = features

    def extract(self, content: ContentItem) -> SemanticFeatures:
        del content
        return self._features
