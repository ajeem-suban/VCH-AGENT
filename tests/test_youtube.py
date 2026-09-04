"""Offline tests for YouTube Atom-feed harvesting."""

import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from vch.config import YouTubeSettings
from vch.youtube import YouTubeHarvestError, YouTubeHarvester, youtube_feed_url


def feed_xml(entries: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/">
  <title>Example Channel</title>
  {entries}
</feed>'''


def entry(video_id: str, published_at: datetime, title: str = "Fresh video") -> str:
    timestamp = published_at.isoformat().replace("+00:00", "Z")
    return f'''<entry>
  <yt:videoId>{video_id}</yt:videoId>
  <title>{title}</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v={video_id}"/>
  <author><name>Example Creator</name></author>
  <published>{timestamp}</published>
  <updated>{timestamp}</updated>
  <media:group>
    <media:title>{title}</media:title>
    <media:description>Video description.</media:description>
    <media:thumbnail url="https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"/>
    <media:community><media:statistics views="123"/></media:community>
  </media:group>
</entry>'''


class YouTubeHarvesterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
        self.settings = YouTubeSettings(
            channel_ids=("UCfirst", "UCsecond"),
            candidate_limit=2,
            max_age_hours=24,
        )

    def test_harvest_merges_fresh_videos_in_time_order(self) -> None:
        feeds = {
            "UCfirst": feed_xml(
                entry("old", self.now - timedelta(hours=25))
                + entry("recent", self.now - timedelta(hours=2))
            ),
            "UCsecond": feed_xml(entry("newest", self.now - timedelta(minutes=30))),
        }

        def fetcher(url: str, timeout: int) -> str:
            self.assertEqual(timeout, 10)
            channel_id = parse_qs(urlparse(url).query)["channel_id"][0]
            return feeds[channel_id]

        result = YouTubeHarvester(self.settings, fetcher, lambda: self.now).harvest()

        self.assertEqual([item.external_id for item in result.candidates], ["newest", "recent"])
        self.assertEqual(result.candidates[0].metadata["view_count"], 123)
        self.assertEqual(result.candidates[0].body, "Video description.")
        self.assertEqual(result.failed_channel_ids, ())

    def test_malformed_entry_and_one_failed_feed_do_not_discard_other_candidates(self) -> None:
        feeds = {
            "UCfirst": feed_xml(entry("valid", self.now) + entry("", self.now)),
        }

        def fetcher(url: str, timeout: int) -> str:
            channel_id = parse_qs(urlparse(url).query)["channel_id"][0]
            if channel_id == "UCsecond":
                raise OSError("network unavailable")
            return feeds[channel_id]

        result = YouTubeHarvester(self.settings, fetcher, lambda: self.now).harvest()

        self.assertEqual([item.external_id for item in result.candidates], ["valid"])
        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(result.failed_channel_ids, ("UCsecond",))

    def test_all_failed_feeds_raise_a_clear_error(self) -> None:
        def fetcher(url: str, timeout: int) -> str:
            raise OSError("network unavailable")

        with self.assertRaises(YouTubeHarvestError):
            YouTubeHarvester(self.settings, fetcher, lambda: self.now).harvest()

    def test_channel_feed_url_uses_the_channel_id_query_parameter(self) -> None:
        self.assertEqual(
            youtube_feed_url("UC test"),
            "https://www.youtube.com/feeds/videos.xml?channel_id=UC+test",
        )
