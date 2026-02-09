"""News scraping module for The Morning Byte."""

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .adapters.youtube import YouTubeAdapter, YouTubeDataAPIAdapter, VideoMetadata
from .utils import get_logger

logger = get_logger(__name__)


@dataclass
class NewsItem:
    """Represents a single news item."""

    title: str
    source: str
    summary: Optional[str] = None
    url: Optional[str] = None
    timestamp: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "title": self.title,
            "source": self.source,
            "summary": self.summary,
            "url": self.url,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class NewsScraper:
    """Scrapes news from X (via Grok) and YouTube trending."""

    def __init__(
        self,
        output_dir: Path,
        youtube_adapter: Optional[YouTubeAdapter] = None,
    ):
        """
        Initialize the news scraper.

        Args:
            output_dir: Directory to store raw news data
            youtube_adapter: Optional YouTubeAdapter instance. If not provided,
                           will attempt to create YouTubeDataAPIAdapter using
                           YOUTUBE_API_KEY environment variable.
        """
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize YouTube adapter
        if youtube_adapter:
            self._youtube = youtube_adapter
        else:
            api_key = os.environ.get("YOUTUBE_API_KEY")
            if api_key:
                try:
                    self._youtube = YouTubeDataAPIAdapter(api_key=api_key)
                except Exception as e:
                    logger.warning(f"Failed to initialize YouTube adapter: {e}")
                    self._youtube = None
            else:
                logger.warning(
                    "YOUTUBE_API_KEY not set. YouTube scraping will be disabled."
                )
                self._youtube = None

    def scrape_x_news(self, query: str = "top 20 tech news last 24h") -> list[NewsItem]:
        """
        Scrape news from X using Grok search via openclaw.

        Args:
            query: Search query for Grok

        Returns:
            List of NewsItem objects
        """
        logger.info(f"Scraping X news with query: {query}")
        output_file = self.output_dir / "x_news.json"

        try:
            result = subprocess.run(
                ["openclaw", "skills", "run", "grok-search", "--query", query],
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                logger.error(f"Grok search failed: {result.stderr}")
                return []

            # Write raw output for debugging
            output_file.write_text(result.stdout)

            # Parse the output
            return self._parse_grok_output(result.stdout)

        except subprocess.TimeoutExpired:
            logger.error("Grok search timed out")
            return []
        except FileNotFoundError:
            logger.error("openclaw CLI not found. Is it installed?")
            return []
        except Exception as e:
            logger.error(f"Error scraping X news: {e}")
            return []

    def scrape_youtube_trending(
        self,
        region_code: str = "US",
        category_id: Optional[str] = None,
        max_results: int = 20,
    ) -> list[NewsItem]:
        """
        Scrape trending videos from YouTube using the Data API.

        Args:
            region_code: ISO 3166-1 alpha-2 country code (default: "US")
            category_id: Optional YouTube category ID (e.g., "28" for Science & Tech)
            max_results: Maximum number of videos to return

        Returns:
            List of NewsItem objects
        """
        logger.info(
            f"Scraping YouTube trending (region={region_code}, "
            f"category={category_id}, max={max_results})"
        )
        output_file = self.output_dir / "youtube_news.json"

        if not self._youtube:
            logger.error(
                "YouTube adapter not initialized. Set YOUTUBE_API_KEY environment variable."
            )
            return []

        try:
            videos = self._youtube.get_trending_videos(
                region_code=region_code,
                category_id=category_id,
                max_results=max_results,
            )

            if not videos:
                logger.warning("No trending videos returned from YouTube API")
                return []

            # Convert to NewsItem objects
            items = self._convert_videos_to_news_items(videos)

            # Save raw data for debugging
            raw_data = [
                {
                    "video_id": v.video_id,
                    "title": v.title,
                    "channel": v.channel_title,
                    "url": v.url,
                    "views": v.view_count,
                    "likes": v.like_count,
                    "published_at": v.published_at.isoformat() if v.published_at else None,
                    "views_per_hour": v.views_per_hour,
                }
                for v in videos
            ]
            output_file.write_text(json.dumps(raw_data, indent=2))

            logger.info(f"Scraped {len(items)} trending videos from YouTube")
            return items

        except Exception as e:
            logger.error(f"Error scraping YouTube: {e}")
            return []

    def _convert_videos_to_news_items(
        self, videos: list[VideoMetadata]
    ) -> list[NewsItem]:
        """Convert VideoMetadata objects to NewsItem objects."""
        items = []
        for video in videos:
            # Create a summary from video metadata
            summary_parts = []
            if video.view_count:
                summary_parts.append(f"{video.view_count:,} views")
            if video.views_per_hour:
                summary_parts.append(f"{video.views_per_hour:,.0f} views/hour")
            if video.channel_title:
                summary_parts.append(f"by {video.channel_title}")

            summary = " | ".join(summary_parts) if summary_parts else None
            if video.description:
                # Truncate description and append
                desc_preview = video.description[:200]
                if len(video.description) > 200:
                    desc_preview += "..."
                summary = f"{summary}\n{desc_preview}" if summary else desc_preview

            items.append(
                NewsItem(
                    title=video.title,
                    source="YouTube",
                    summary=summary,
                    url=video.url,
                    timestamp=video.published_at,
                )
            )
        return items

    def _parse_grok_output(self, output: str) -> list[NewsItem]:
        """Parse Grok search output into NewsItem objects."""
        items = []
        try:
            data = json.loads(output)
            if isinstance(data, list):
                for item in data:
                    items.append(
                        NewsItem(
                            title=item.get("title", ""),
                            source="X/Grok",
                            summary=item.get("summary"),
                            url=item.get("url"),
                        )
                    )
            elif isinstance(data, dict):
                # Handle different response formats
                news_list = data.get("news", data.get("results", []))
                for item in news_list:
                    items.append(
                        NewsItem(
                            title=item.get("title", ""),
                            source="X/Grok",
                            summary=item.get("summary"),
                            url=item.get("url"),
                        )
                    )
        except json.JSONDecodeError:
            # Try to parse as plain text
            logger.warning("Could not parse Grok output as JSON, treating as text")
            items.append(
                NewsItem(
                    title="Raw news from X",
                    source="X/Grok",
                    summary=output[:1000] if output else None,
                )
            )
        return items

    def scrape_all(self) -> list[NewsItem]:
        """
        Scrape news from all sources.

        Returns:
            Combined list of NewsItem objects from all sources
        """
        logger.info("Starting full news scrape...")

        all_news = []
        all_news.extend(self.scrape_x_news())
        # Fetch Science & Technology trending videos (category_id="28")
        all_news.extend(
            self.scrape_youtube_trending(
                category_id=YouTubeDataAPIAdapter.CATEGORY_SCIENCE_TECH
            )
        )

        logger.info(f"Scraped {len(all_news)} news items total")

        # Save combined results
        combined_file = self.output_dir / "raw_news.json"
        combined_data = [item.to_dict() for item in all_news]
        combined_file.write_text(json.dumps(combined_data, indent=2))

        return all_news

    def load_from_file(self, filepath: Path) -> list[NewsItem]:
        """
        Load news items from a previously saved JSON file.

        Args:
            filepath: Path to the JSON file

        Returns:
            List of NewsItem objects
        """
        try:
            data = json.loads(filepath.read_text())
            items = []
            for item in data:
                items.append(
                    NewsItem(
                        title=item.get("title", ""),
                        source=item.get("source", "Unknown"),
                        summary=item.get("summary"),
                        url=item.get("url"),
                    )
                )
            return items
        except Exception as e:
            logger.error(f"Error loading news from file: {e}")
            return []
