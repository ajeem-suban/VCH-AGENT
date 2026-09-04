"""The first vertical slice: normalized content to persisted features."""

import logging
from dataclasses import dataclass
from typing import Iterable

from .database import VchDatabase
from .domain import ContentItem, SemanticFeatures, StoredFeatureRecord
from .extractors import FeatureExtractionError, SemanticFeatureExtractor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeatureExtractionBatchResult:
    """Successful feature snapshots and content-specific extraction failures."""

    records: list[StoredFeatureRecord]
    failed_count: int


class FeatureExtractionService:
    """Coordinates validation, extraction, and immutable persistence."""

    def __init__(self, database: VchDatabase, extractor: SemanticFeatureExtractor) -> None:
        self._database = database
        self._extractor = extractor

    def analyze(self, content: ContentItem) -> StoredFeatureRecord:
        """Extract and store features using information available at analysis time."""

        features = self._extractor.extract(content)
        content_id = self._database.store_content(content)
        return self._database.store_features(content_id, features, self._extractor.version)

    def analyze_batch(self, content_items: Iterable[ContentItem]) -> FeatureExtractionBatchResult:
        """Persist a candidate pool and analyze it with one reusable extractor."""

        contents = list(content_items)
        content_ids = self._database.store_content_batch(contents)
        successful_features: list[tuple[int, SemanticFeatures]] = []
        failed_count = 0

        for content, content_id in zip(contents, content_ids, strict=True):
            try:
                successful_features.append((content_id, self._extractor.extract(content)))
            except FeatureExtractionError as error:
                failed_count += 1
                logger.warning("Skipping semantic features for %s: %s", content.external_id, error)

        records = self._database.store_feature_batch(
            successful_features,
            self._extractor.version,
        )
        return FeatureExtractionBatchResult(records=records, failed_count=failed_count)
