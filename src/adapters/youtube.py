"""YouTube adapter interface and implementations.

This module provides an abstraction layer for YouTube data access,
allowing for future extensibility:
- Channel-based tracking
- Category filtering
- Transcript ingestion
- "Top per channel" logic
- Ranking by engagement velocity (views/hour)
"""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ..utils import get_logger

logger = get_logger(__name__)

# YouTube Data API quota costs (for reference):
# - videos.list: 1 unit
# - search.list: 100 units
# - Default daily quota: 10,000 units


@dataclass
class VideoMetadata:
    """Structured metadata for a YouTube video."""

    video_id: str
    title: str
    channel_id: str
    channel_title: str
    description: str
    published_at: Optional[datetime] = None
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    duration: Optional[str] = None
    category_id: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    thumbnail_url: Optional[str] = None

    @property
    def url(self) -> str:
        """Return the full YouTube URL for this video."""
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def views_per_hour(self) -> Optional[float]:
        """Calculate engagement velocity (views per hour since publish)."""
        if not self.published_at or self.view_count == 0:
            return None
        hours_since_publish = (
            datetime.now(self.published_at.tzinfo) - self.published_at
        ).total_seconds() / 3600
        if hours_since_publish <= 0:
            return None
        return self.view_count / hours_since_publish


class YouTubeAdapter(ABC):
    """Abstract base class for YouTube data access.

    Implement this interface to support different data sources:
    - YouTube Data API (official)
    - yt-dlp for metadata extraction
    - Web scraping fallback
    - Mock adapter for testing
    """

    @abstractmethod
    def get_trending_videos(
        self,
        region_code: str = "US",
        category_id: Optional[str] = None,
        max_results: int = 20,
    ) -> list[VideoMetadata]:
        """Fetch trending/most popular videos.

        Args:
            region_code: ISO 3166-1 alpha-2 country code
            category_id: YouTube video category ID (e.g., "28" for Science & Tech)
            max_results: Maximum number of videos to return (max 50)

        Returns:
            List of VideoMetadata objects
        """
        pass

    @abstractmethod
    def get_channel_videos(
        self,
        channel_id: str,
        max_results: int = 10,
        order: str = "date",
    ) -> list[VideoMetadata]:
        """Fetch recent videos from a specific channel.

        Args:
            channel_id: YouTube channel ID
            max_results: Maximum number of videos to return
            order: Sort order ("date", "viewCount", "rating")

        Returns:
            List of VideoMetadata objects
        """
        pass

    @abstractmethod
    def get_video_details(self, video_ids: list[str]) -> list[VideoMetadata]:
        """Fetch detailed metadata for specific videos.

        Args:
            video_ids: List of YouTube video IDs

        Returns:
            List of VideoMetadata objects with full details
        """
        pass


class YouTubeDataAPIAdapter(YouTubeAdapter):
    """YouTube Data API v3 implementation.

    Quota-efficient implementation using videos.list (1 unit per call)
    instead of search.list (100 units per call) where possible.
    """

    # Common video category IDs for reference
    CATEGORY_SCIENCE_TECH = "28"
    CATEGORY_NEWS_POLITICS = "25"
    CATEGORY_EDUCATION = "27"
    CATEGORY_ENTERTAINMENT = "24"

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the YouTube Data API client.

        Args:
            api_key: YouTube Data API key. If not provided, reads from
                     YOUTUBE_API_KEY environment variable.
        """
        self.api_key = api_key or os.environ.get("YOUTUBE_API_KEY")
        if not self.api_key:
            raise ValueError(
                "YouTube API key required. Set YOUTUBE_API_KEY environment variable "
                "or pass api_key parameter."
            )
        self._youtube = build("youtube", "v3", developerKey=self.api_key)

    def get_trending_videos(
        self,
        region_code: str = "US",
        category_id: Optional[str] = None,
        max_results: int = 20,
    ) -> list[VideoMetadata]:
        """Fetch trending videos using videos.list with chart=mostPopular.

        This is quota-efficient (1 unit) compared to search.list (100 units).
        """
        try:
            request = self._youtube.videos().list(
                part="snippet,statistics,contentDetails",
                chart="mostPopular",
                regionCode=region_code,
                videoCategoryId=category_id,
                maxResults=min(max_results, 50),
            )
            response = request.execute()
            return self._parse_video_list_response(response)

        except HttpError as e:
            logger.error(f"YouTube API error fetching trending: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error fetching trending videos: {e}")
            return []

    def get_channel_videos(
        self,
        channel_id: str,
        max_results: int = 10,
        order: str = "date",
    ) -> list[VideoMetadata]:
        """Fetch recent videos from a channel.

        Note: This uses search.list (100 units) - use sparingly.
        For production, consider caching or using channel uploads playlist.
        """
        try:
            # First, get the uploads playlist ID (more quota efficient)
            channel_request = self._youtube.channels().list(
                part="contentDetails",
                id=channel_id,
            )
            channel_response = channel_request.execute()

            if not channel_response.get("items"):
                logger.warning(f"Channel not found: {channel_id}")
                return []

            uploads_playlist_id = channel_response["items"][0]["contentDetails"][
                "relatedPlaylists"
            ]["uploads"]

            # Get videos from uploads playlist (3 units vs 100 for search)
            playlist_request = self._youtube.playlistItems().list(
                part="snippet",
                playlistId=uploads_playlist_id,
                maxResults=min(max_results, 50),
            )
            playlist_response = playlist_request.execute()

            video_ids = [
                item["snippet"]["resourceId"]["videoId"]
                for item in playlist_response.get("items", [])
            ]

            if not video_ids:
                return []

            # Get full video details
            return self.get_video_details(video_ids)

        except HttpError as e:
            logger.error(f"YouTube API error fetching channel videos: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error fetching channel videos: {e}")
            return []

    def get_video_details(self, video_ids: list[str]) -> list[VideoMetadata]:
        """Fetch detailed metadata for specific videos.

        Quota cost: 1 unit per call (can batch up to 50 videos).
        """
        if not video_ids:
            return []

        try:
            # Batch video IDs (max 50 per request)
            all_videos = []
            for i in range(0, len(video_ids), 50):
                batch = video_ids[i : i + 50]
                request = self._youtube.videos().list(
                    part="snippet,statistics,contentDetails",
                    id=",".join(batch),
                )
                response = request.execute()
                all_videos.extend(self._parse_video_list_response(response))

            return all_videos

        except HttpError as e:
            logger.error(f"YouTube API error fetching video details: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error fetching video details: {e}")
            return []

    def get_trending_by_engagement_velocity(
        self,
        region_code: str = "US",
        category_id: Optional[str] = None,
        max_results: int = 20,
        min_views_per_hour: float = 1000,
    ) -> list[VideoMetadata]:
        """Fetch trending videos sorted by engagement velocity.

        This identifies rapidly growing videos that may not yet be
        in the top trending list but are gaining traction quickly.
        """
        videos = self.get_trending_videos(
            region_code=region_code,
            category_id=category_id,
            max_results=50,  # Fetch more to filter
        )

        # Filter and sort by views per hour
        scored_videos = [
            (v, v.views_per_hour)
            for v in videos
            if v.views_per_hour and v.views_per_hour >= min_views_per_hour
        ]
        scored_videos.sort(key=lambda x: x[1], reverse=True)

        return [v for v, _ in scored_videos[:max_results]]

    def _parse_video_list_response(
        self, response: dict
    ) -> list[VideoMetadata]:
        """Parse YouTube API videos.list response into VideoMetadata objects."""
        videos = []

        for item in response.get("items", []):
            snippet = item.get("snippet", {})
            statistics = item.get("statistics", {})
            content_details = item.get("contentDetails", {})

            # Parse publish date
            published_at = None
            if snippet.get("publishedAt"):
                try:
                    published_at = datetime.fromisoformat(
                        snippet["publishedAt"].replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            # Get best thumbnail
            thumbnails = snippet.get("thumbnails", {})
            thumbnail_url = (
                thumbnails.get("maxres", {}).get("url")
                or thumbnails.get("high", {}).get("url")
                or thumbnails.get("medium", {}).get("url")
                or thumbnails.get("default", {}).get("url")
            )

            videos.append(
                VideoMetadata(
                    video_id=item["id"],
                    title=snippet.get("title", ""),
                    channel_id=snippet.get("channelId", ""),
                    channel_title=snippet.get("channelTitle", ""),
                    description=snippet.get("description", ""),
                    published_at=published_at,
                    view_count=int(statistics.get("viewCount", 0)),
                    like_count=int(statistics.get("likeCount", 0)),
                    comment_count=int(statistics.get("commentCount", 0)),
                    duration=content_details.get("duration"),
                    category_id=snippet.get("categoryId"),
                    tags=snippet.get("tags", []),
                    thumbnail_url=thumbnail_url,
                )
            )

        return videos
