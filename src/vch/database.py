"""Small SQLite persistence layer for content and feature snapshots."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .domain import ContentItem, SemanticFeatures, StoredFeatureRecord, TranscriptResult


class VchDatabase:
    """Persist immutable analysis inputs and semantic features locally."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS content (
                    id INTEGER PRIMARY KEY,
                    platform TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    url TEXT NOT NULL,
                    author_or_channel TEXT,
                    community_or_channel TEXT,
                    published_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    UNIQUE(platform, external_id)
                );

                CREATE TABLE IF NOT EXISTS semantic_features (
                    id INTEGER PRIMARY KEY,
                    content_id INTEGER NOT NULL REFERENCES content(id),
                    extracted_at TEXT NOT NULL,
                    extractor_version TEXT NOT NULL,
                    features_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS transcript_records (
                    id INTEGER PRIMARY KEY,
                    content_id INTEGER NOT NULL REFERENCES content(id),
                    fetched_at TEXT NOT NULL,
                    available INTEGER NOT NULL,
                    language_code TEXT,
                    is_generated INTEGER,
                    is_truncated INTEGER NOT NULL,
                    error_type TEXT,
                    transcript_text TEXT
                );
                """
            )

    def store_content(self, content: ContentItem) -> int:
        """Store new source content once and return its local identifier."""

        return self.store_content_batch([content])[0]

    def store_content_batch(self, content_items: Iterable[ContentItem]) -> list[int]:
        """Store a harvested candidate pool with one SQLite connection."""

        contents = list(content_items)
        if not contents:
            return []

        identifiers: list[int] = []
        with self._connect() as connection:
            for content in contents:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO content (
                        platform, external_id, title, body, url, author_or_channel,
                        community_or_channel, published_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        content.platform.value,
                        content.external_id,
                        content.title,
                        content.body,
                        str(content.url),
                        content.author_or_channel,
                        content.community_or_channel,
                        content.published_at.isoformat(),
                        json.dumps(content.metadata, sort_keys=True),
                    ),
                )
                row = connection.execute(
                    "SELECT id FROM content WHERE platform = ? AND external_id = ?",
                    (content.platform.value, content.external_id),
                ).fetchone()
                if row is None:
                    raise RuntimeError("Stored content could not be retrieved.")
                identifiers.append(int(row["id"]))
        return identifiers

    def store_features(
        self,
        content_id: int,
        features: SemanticFeatures,
        extractor_version: str,
        extracted_at: datetime | None = None,
    ) -> StoredFeatureRecord:
        """Append a feature snapshot; never overwrite past extractions."""

        return self.store_feature_batch(
            [(content_id, features)],
            extractor_version,
            extracted_at,
        )[0]

    def store_feature_batch(
        self,
        feature_items: Iterable[tuple[int, SemanticFeatures]],
        extractor_version: str,
        extracted_at: datetime | None = None,
    ) -> list[StoredFeatureRecord]:
        """Append feature snapshots with one SQLite connection."""

        items = list(feature_items)
        if not items:
            return []
        timestamp = extracted_at or datetime.now(timezone.utc)
        records: list[StoredFeatureRecord] = []
        with self._connect() as connection:
            for content_id, features in items:
                cursor = connection.execute(
                    """
                    INSERT INTO semantic_features (
                        content_id, extracted_at, extractor_version, features_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        content_id,
                        timestamp.isoformat(),
                        extractor_version,
                        features.model_dump_json(),
                    ),
                )
                records.append(
                    StoredFeatureRecord(
                        id=int(cursor.lastrowid),
                        content_id=content_id,
                        extracted_at=timestamp,
                        extractor_version=extractor_version,
                        features=features,
                    )
                )
        return records

    def store_transcript_batch(
        self,
        transcript_items: Iterable[tuple[int, TranscriptResult]],
        fetched_at: datetime | None = None,
    ) -> int:
        """Append transcript outcomes, including unavailable transcripts."""

        items = list(transcript_items)
        if not items:
            return 0
        timestamp = fetched_at or datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO transcript_records (
                    content_id, fetched_at, available, language_code, is_generated,
                    is_truncated, error_type, transcript_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        content_id,
                        timestamp.isoformat(),
                        int(result.available),
                        result.language_code,
                        None if result.is_generated is None else int(result.is_generated),
                        int(result.is_truncated),
                        result.error_type,
                        result.text,
                    )
                    for content_id, result in items
                ],
            )
        return len(items)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()
