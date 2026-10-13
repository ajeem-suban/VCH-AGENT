"""Small SQLite persistence layer for content and feature snapshots."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .domain import (
    CollectionStatus, ContentItem, MetricSnapshot, OutcomeRecord, OutcomeStatus,
    PredictionRecord, SemanticFeatures, StoredFeatureRecord, TranscriptResult,
)


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

                CREATE TABLE IF NOT EXISTS metric_snapshots (
                    id INTEGER PRIMARY KEY,
                    content_id INTEGER NOT NULL REFERENCES content(id),
                    platform TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    source_published_at TEXT,
                    reddit_score INTEGER, reddit_comment_count INTEGER,
                    reddit_upvote_ratio REAL, youtube_view_count INTEGER,
                    youtube_like_count INTEGER, youtube_comment_count INTEGER,
                    collector_version TEXT NOT NULL, status TEXT NOT NULL,
                    error_message TEXT, role TEXT NOT NULL DEFAULT 't0'
                );
                CREATE INDEX IF NOT EXISTS idx_metric_snapshots_content_time
                    ON metric_snapshots(content_id, observed_at);
                CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY, content_id INTEGER NOT NULL REFERENCES content(id),
                    predicted_at TEXT NOT NULL, horizon_hours INTEGER NOT NULL,
                    predictor_version TEXT NOT NULL, interpretation TEXT NOT NULL,
                    value REAL, feature_record_id INTEGER REFERENCES semantic_features(id),
                    eligible_for_evaluation INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_predictions_content_time ON predictions(content_id, predicted_at);
                CREATE TABLE IF NOT EXISTS outcomes (
                    id INTEGER PRIMARY KEY, content_id INTEGER NOT NULL REFERENCES content(id),
                    horizon_hours INTEGER NOT NULL, target_at TEXT NOT NULL, observed_at TEXT,
                    baseline_snapshot_id INTEGER NOT NULL REFERENCES metric_snapshots(id),
                    follow_up_snapshot_id INTEGER REFERENCES metric_snapshots(id),
                    status TEXT NOT NULL, growth_json TEXT NOT NULL, explanation TEXT,
                    attempted_at TEXT, retryable INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(content_id, horizon_hours)
                );
                CREATE INDEX IF NOT EXISTS idx_outcomes_pending ON outcomes(status, target_at);
                """
            )
            self._add_column_if_missing(connection, "metric_snapshots", "role", "TEXT NOT NULL DEFAULT 't0'")
            self._add_column_if_missing(connection, "outcomes", "attempted_at", "TEXT")
            self._add_column_if_missing(connection, "outcomes", "retryable", "INTEGER NOT NULL DEFAULT 0")

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
        if timestamp.tzinfo is None:
            raise ValueError("extracted_at must include a timezone.")
        timestamp = timestamp.astimezone(timezone.utc)
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

    def store_metric_snapshot(self, snapshot: MetricSnapshot) -> MetricSnapshot:
        """Append an observation. Existing observations are never changed."""
        data = snapshot.model_dump()
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO metric_snapshots (content_id, platform, observed_at, source_published_at,
                reddit_score, reddit_comment_count, reddit_upvote_ratio, youtube_view_count,
                youtube_like_count, youtube_comment_count, collector_version, status, error_message, role)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (data["content_id"], data["platform"].value, data["observed_at"].isoformat(),
                 data["source_published_at"].isoformat() if data["source_published_at"] else None,
                 data["reddit_score"], data["reddit_comment_count"], data["reddit_upvote_ratio"],
                 data["youtube_view_count"], data["youtube_like_count"], data["youtube_comment_count"],
                 data["collector_version"], data["status"].value, data["error_message"], data["role"]),
            )
        return snapshot.model_copy(update={"id": int(cursor.lastrowid)})

    def list_metric_snapshots(self, content_id: int | None = None) -> list[MetricSnapshot]:
        query = "SELECT * FROM metric_snapshots"
        parameters: tuple[object, ...] = ()
        if content_id is not None:
            query += " WHERE content_id = ?"
            parameters = (content_id,)
        query += " ORDER BY observed_at, id"
        with self._connect() as connection:
            return [self._snapshot_from_row(row) for row in connection.execute(query, parameters)]

    def content_reference(self, content_id: int) -> tuple[str, str] | None:
        """Return the stored platform and source ID without reconstructing content."""
        with self._connect() as connection:
            row = connection.execute("SELECT platform, external_id FROM content WHERE id = ?", (content_id,)).fetchone()
        return (str(row["platform"]), str(row["external_id"])) if row else None

    def store_prediction(self, prediction: PredictionRecord) -> PredictionRecord:
        """Store a prediction only when referenced features predate it."""
        with self._connect() as connection:
            if prediction.feature_record_id is not None:
                feature = connection.execute("SELECT content_id, extracted_at FROM semantic_features WHERE id = ?", (prediction.feature_record_id,)).fetchone()
                if feature is None or feature["content_id"] != prediction.content_id or _parse_time(feature["extracted_at"]) > prediction.predicted_at:
                    raise ValueError("Prediction cannot reference features unavailable at prediction time.")
            cursor = connection.execute("""INSERT INTO predictions (content_id, predicted_at, horizon_hours, predictor_version, interpretation, value, feature_record_id, eligible_for_evaluation)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (prediction.content_id, prediction.predicted_at.isoformat(), prediction.horizon_hours, prediction.predictor_version, prediction.interpretation, prediction.value, prediction.feature_record_id, int(prediction.eligible_for_evaluation)))
        return prediction.model_copy(update={"id": int(cursor.lastrowid)})

    def get_or_create_outcome(self, content_id: int, horizon_hours: int, baseline: MetricSnapshot) -> OutcomeRecord:
        target = baseline.observed_at + __import__("datetime").timedelta(hours=horizon_hours)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM outcomes WHERE content_id = ? AND horizon_hours = ?", (content_id, horizon_hours)).fetchone()
            if row:
                return self._outcome_from_row(row)
            cursor = connection.execute("""INSERT INTO outcomes (content_id, horizon_hours, target_at, baseline_snapshot_id, status, growth_json)
                VALUES (?, ?, ?, ?, ?, ?)""", (content_id, horizon_hours, target.isoformat(), baseline.id, OutcomeStatus.PENDING.value, "{}"))
        return OutcomeRecord(id=int(cursor.lastrowid), content_id=content_id, horizon_hours=horizon_hours, target_at=target, baseline_snapshot_id=baseline.id or 0)

    def complete_outcome(self, outcome: OutcomeRecord) -> OutcomeRecord:
        """Atomically link a later snapshot; it may never be a pre-target input."""
        with self._connect() as connection:
            if outcome.follow_up_snapshot_id is not None:
                follow_up = connection.execute("SELECT observed_at FROM metric_snapshots WHERE id = ?", (outcome.follow_up_snapshot_id,)).fetchone()
                if follow_up is None or _parse_time(follow_up["observed_at"]) < outcome.target_at:
                    raise ValueError("Outcome follow-up must be observed at or after its target time.")
            connection.execute("""UPDATE outcomes SET observed_at=?, follow_up_snapshot_id=?, status=?, growth_json=?, explanation=?, attempted_at=?, retryable=? WHERE id=?""", (outcome.observed_at.isoformat() if outcome.observed_at else None, outcome.follow_up_snapshot_id, outcome.status.value, json.dumps({key: value.model_dump() for key, value in outcome.growth.items()}), outcome.explanation, outcome.attempted_at.isoformat() if outcome.attempted_at else None, int(outcome.retryable), outcome.id))
        return outcome

    def list_outcomes(self) -> list[OutcomeRecord]:
        with self._connect() as connection:
            return [self._outcome_from_row(row) for row in connection.execute("SELECT * FROM outcomes ORDER BY target_at")]

    @staticmethod
    def _snapshot_from_row(row: sqlite3.Row) -> MetricSnapshot:
        return MetricSnapshot(id=row["id"], content_id=row["content_id"], platform=row["platform"], observed_at=_parse_time(row["observed_at"]), source_published_at=_parse_time(row["source_published_at"]) if row["source_published_at"] else None, reddit_score=row["reddit_score"], reddit_comment_count=row["reddit_comment_count"], reddit_upvote_ratio=row["reddit_upvote_ratio"], youtube_view_count=row["youtube_view_count"], youtube_like_count=row["youtube_like_count"], youtube_comment_count=row["youtube_comment_count"], collector_version=row["collector_version"], status=CollectionStatus(row["status"]), error_message=row["error_message"], role=row["role"])

    @staticmethod
    def _outcome_from_row(row: sqlite3.Row) -> OutcomeRecord:
        return OutcomeRecord(id=row["id"], content_id=row["content_id"], horizon_hours=row["horizon_hours"], target_at=_parse_time(row["target_at"]), observed_at=_parse_time(row["observed_at"]) if row["observed_at"] else None, baseline_snapshot_id=row["baseline_snapshot_id"], follow_up_snapshot_id=row["follow_up_snapshot_id"], status=OutcomeStatus(row["status"]), growth=json.loads(row["growth_json"]), explanation=row["explanation"], attempted_at=_parse_time(row["attempted_at"]) if row["attempted_at"] else None, retryable=bool(row["retryable"]))

    @staticmethod
    def _add_column_if_missing(connection: sqlite3.Connection, table: str, name: str, definition: str) -> None:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if name not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Legacy stored timestamps must include a timezone; migration is required.")
    return parsed.astimezone(timezone.utc)
