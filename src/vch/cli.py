"""Command-line entry points for local VCH backend operations."""

import argparse
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
from .reddit import RedditHarvestError, RedditHarvester
from .service import FeatureExtractionService
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
    return parser


def _harvest_reddit() -> int:
    try:
        harvest = RedditHarvester(load_reddit_settings()).harvest()
        database = VchDatabase(load_database_path())
        database.initialize()
        database.store_content_batch(harvest.candidates)
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
        database.store_content_batch(harvest.candidates)
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
