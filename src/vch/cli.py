"""Command-line entry points for local VCH backend operations."""

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence

from .config import (
    ConfigurationError,
    load_database_path,
    load_local_llm_settings,
    load_reddit_settings,
    load_youtube_settings,
)
from .database import VchDatabase
from .local_llm import LlamaCppFeatureExtractor, LocalLlmUnavailableError
from .reddit import RedditHarvestError, RedditHarvester, RedditMetricCollector
from .service import FeatureExtractionService, OutcomeService, evaluation_summary, initial_metric_snapshot
from .youtube import YouTubeHarvestError, YouTubeHarvester


def main(argv: Sequence[str] | None = None) -> int:
    """Run a VCH command and return a shell-compatible exit code."""

    parser = _build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "harvest" and arguments.source == "reddit":
        return _harvest_reddit()
    if arguments.command == "harvest" and arguments.source == "youtube":
        return _harvest_youtube()
    if arguments.command == "scan" and arguments.source == "reddit":
        return _scan_reddit()
    if arguments.command == "scan" and arguments.source == "youtube":
        return _scan_youtube()
    if arguments.command == "outcomes" and arguments.outcome_command == "status":
        return _outcome_status()
    if arguments.command == "outcomes" and arguments.outcome_command == "evaluate":
        return _outcome_evaluate()
    if arguments.command == "outcomes" and arguments.outcome_command == "collect":
        return _collect_outcomes(int(arguments.horizon.removesuffix("h")))
    parser.print_help()
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Viral Content Hunter backend")
    commands = parser.add_subparsers(dest="command")
    harvest = commands.add_parser("harvest", help="collect fresh source content")
    harvest_sources = harvest.add_subparsers(dest="source")
    harvest_sources.add_parser("reddit", help="collect fresh Reddit posts")
    harvest_sources.add_parser("youtube", help="collect fresh YouTube videos")
    scan = commands.add_parser("scan", help="collect and analyze fresh source content")
    scan_sources = scan.add_subparsers(dest="source")
    scan_sources.add_parser("reddit", help="scan fresh Reddit posts")
    scan_sources.add_parser("youtube", help="scan fresh YouTube videos")
    outcomes = commands.add_parser("outcomes", help="inspect recorded engagement outcomes")
    outcome_commands = outcomes.add_subparsers(dest="outcome_command")
    outcome_commands.add_parser("status", help="show metric snapshot and outcome coverage")
    outcome_commands.add_parser("evaluate", help="summarize recorded outcomes (not model accuracy)")
    collect = outcome_commands.add_parser("collect", help="collect due observations (requires a configured refresher)")
    collect.add_argument("--horizon", choices=("24h", "72h"), required=True, help="outcome horizon")
    return parser


def _harvest_reddit() -> int:
    try:
        harvest = RedditHarvester(load_reddit_settings()).harvest()
        database = VchDatabase(load_database_path())
        database.initialize()
        _store_harvest_with_snapshots(database, harvest.candidates)
    except (ConfigurationError, RedditHarvestError, sqlite3.Error, OSError) as error:
        print(f"Harvest failed: {error}", file=sys.stderr)
        return 1

    print(
        f"Harvested {len(harvest.candidates)} fresh Reddit candidates; "
        f"skipped {harvest.skipped_count} invalid candidates."
    )
    return 0


def _scan_reddit() -> int:
    try:
        harvest = RedditHarvester(load_reddit_settings()).harvest()
        database = VchDatabase(load_database_path())
        database.initialize()
        extractor = LlamaCppFeatureExtractor(load_local_llm_settings())
        analysis = FeatureExtractionService(database, extractor).analyze_batch(harvest.candidates)
        _store_harvest_with_snapshots(database, harvest.candidates)
    except (
        ConfigurationError,
        LocalLlmUnavailableError,
        RedditHarvestError,
        sqlite3.Error,
        OSError,
    ) as error:
        print(f"Scan failed: {error}", file=sys.stderr)
        return 1

    skipped_count = harvest.skipped_count + analysis.failed_count
    print(
        f"Scanned {len(harvest.candidates)} fresh Reddit candidates; "
        f"stored {len(analysis.records)} feature snapshots; "
        f"skipped {skipped_count} source or feature candidates."
    )
    return 0


def _harvest_youtube() -> int:
    try:
        harvest = YouTubeHarvester(load_youtube_settings()).harvest()
        database = VchDatabase(load_database_path())
        database.initialize()
        _store_harvest_with_snapshots(database, harvest.candidates)
    except (ConfigurationError, YouTubeHarvestError, sqlite3.Error, OSError) as error:
        print(f"Harvest failed: {error}", file=sys.stderr)
        return 1

    print(
        f"Harvested {len(harvest.candidates)} fresh YouTube candidates; "
        f"skipped {harvest.skipped_count} invalid candidates; "
        f"failed {len(harvest.failed_channel_ids)} channel feeds."
    )
    return 0


def _scan_youtube() -> int:
    try:
        harvest = YouTubeHarvester(load_youtube_settings()).harvest()
        database = VchDatabase(load_database_path())
        database.initialize()
        extractor = LlamaCppFeatureExtractor(load_local_llm_settings())
        analysis = FeatureExtractionService(database, extractor).analyze_batch(harvest.candidates)
        _store_harvest_with_snapshots(database, harvest.candidates)
    except (
        ConfigurationError,
        LocalLlmUnavailableError,
        YouTubeHarvestError,
        sqlite3.Error,
        OSError,
    ) as error:
        print(f"Scan failed: {error}", file=sys.stderr)
        return 1

    skipped_count = harvest.skipped_count + analysis.failed_count
    print(
        f"Scanned {len(harvest.candidates)} fresh YouTube candidates; "
        f"stored {len(analysis.records)} feature snapshots; "
        f"skipped {skipped_count} source or feature candidates; "
        f"failed {len(harvest.failed_channel_ids)} channel feeds."
    )
    return 0


def _store_harvest_with_snapshots(database: VchDatabase, candidates: list) -> None:
    """Persist one T0 observation per run without overwriting historical metrics."""
    for content, content_id in zip(candidates, database.store_content_batch(candidates), strict=True):
        database.store_metric_snapshot(initial_metric_snapshot(content, content_id))


def _outcome_status() -> int:
    try:
        database = VchDatabase(load_database_path())
        database.initialize()
        print(json.dumps(evaluation_summary(database), sort_keys=True))
        return 0
    except (ConfigurationError, sqlite3.Error, OSError) as error:
        print(f"Outcome status failed: {error}", file=sys.stderr)
        return 1


def _outcome_evaluate() -> int:
    return _outcome_status()


def _collect_outcomes(horizon_hours: int) -> int:
    """Refresh only due Reddit outcomes; unsupported platforms remain untouched."""
    try:
        database = VchDatabase(load_database_path())
        database.initialize()
        collector = RedditMetricCollector(database, load_reddit_settings())
        result = OutcomeService(database, collector).collect_due(horizon_hours)
    except (ConfigurationError, sqlite3.Error, OSError, RedditHarvestError) as error:
        print(f"Outcome collection failed: {error}", file=sys.stderr)
        return 1
    print(f"Processed {horizon_hours}h outcomes: observed {result.observed}; pending {result.pending}; unavailable {result.unavailable}; failed {result.failed}.")
    return 1 if result.failed and not result.observed else 0
