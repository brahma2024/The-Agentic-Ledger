"""RSS feed fetching with caching, bundle awareness, and hybrid fallback for The Agentic Ledger."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from urllib.parse import quote_plus

import feedparser
import requests

from ..scraper import NewsItem
from ..utils import get_logger

if TYPE_CHECKING:
    from .feed_bundles import BundledNewsItem, BundleManager

logger = get_logger(__name__)

# Browser-like User-Agent to avoid being blocked by Google
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

GOOGLE_NEWS_RSS_BASE = "https://news.google.com/rss/search"


@dataclass
class FeedCache:
    """Cache entry for an RSS feed."""

    url: str
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    items: list[dict] = field(default_factory=list)
    fetched_at: Optional[datetime] = None

    def is_expired(self, ttl_minutes: int) -> bool:
        """Check if the cache entry has expired."""
        if not self.fetched_at:
            return True
        return datetime.now() - self.fetched_at > timedelta(minutes=ttl_minutes)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "url": self.url,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "items": self.items,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FeedCache":
        """Create from dictionary."""
        fetched_at = None
        if data.get("fetched_at"):
            fetched_at = datetime.fromisoformat(data["fetched_at"])
        return cls(
            url=data["url"],
            etag=data.get("etag"),
            last_modified=data.get("last_modified"),
            items=data.get("items", []),
            fetched_at=fetched_at,
        )


class RSSFetcher:
    """Fetches and caches RSS feeds with bundle awareness and hybrid fallback."""

    def __init__(
        self,
        bundle_manager: "BundleManager",
        cache_dir: Optional[Path] = None,
        cache_ttl_minutes: int = 30,
    ):
        self.bundle_manager = bundle_manager
        self.feed_urls = bundle_manager.get_all_feed_urls()
        self.cache_dir = cache_dir or Path("temp")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_minutes = cache_ttl_minutes
        self._seen_urls: set[str] = set()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})

    def _get_cache_path(self, url: str) -> Path:
        """Get the cache file path for a feed URL."""
        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        return self.cache_dir / f"rss_cache_{url_hash}.json"

    def _load_cache(self, url: str) -> Optional[FeedCache]:
        """Load cache entry for a feed URL."""
        cache_path = self._get_cache_path(url)
        if not cache_path.exists():
            return None
        try:
            data = json.loads(cache_path.read_text())
            return FeedCache.from_dict(data)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to load cache for {url}: {e}")
            return None

    def _save_cache(self, cache: FeedCache) -> None:
        """Save cache entry for a feed."""
        cache_path = self._get_cache_path(cache.url)
        try:
            cache_path.write_text(json.dumps(cache.to_dict(), indent=2))
        except Exception as e:
            logger.warning(f"Failed to save cache for {cache.url}: {e}")

    def _http_get(self, url: str, etag: Optional[str] = None, modified: Optional[str] = None) -> requests.Response:
        """Fetch URL with requests, supporting conditional GET headers."""
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if modified:
            headers["If-Modified-Since"] = modified
        return self._session.get(url, headers=headers, timeout=15)

    def _parse_feed_response(self, response: requests.Response) -> feedparser.FeedParserDict:
        """Parse HTTP response content with feedparser."""
        return feedparser.parse(response.content)

    def _extract_query_from_alert_feed(self, feed: feedparser.FeedParserDict) -> Optional[str]:
        """Extract the search query from a Google Alert feed title.

        Google Alert feeds have titles like:
          'Google Alert - "transformer architecture" OR "reasoning capability" ...'
        """
        title = feed.feed.get("title", "")
        prefix = "Google Alert - "
        if title.startswith(prefix):
            return title[len(prefix):]
        return None

    def _build_google_news_url(self, query: str) -> str:
        """Build a Google News RSS search URL from a query string."""
        return f"{GOOGLE_NEWS_RSS_BASE}?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"

    def _entries_to_items(self, entries: list) -> tuple[list[NewsItem], list[dict]]:
        """Convert feedparser entries to NewsItem list and cache dicts."""
        items = []
        cache_items = []

        for entry in entries:
            link = entry.get("link", "")
            if link and link in self._seen_urls:
                continue
            if link:
                self._seen_urls.add(link)

            timestamp = None
            if entry.get("published_parsed"):
                try:
                    timestamp = datetime(*entry.published_parsed[:6])
                except Exception:
                    pass

            # Determine source from feed entry
            source = entry.get("source", {}).get("title", "RSS") if isinstance(entry.get("source"), dict) else "RSS"

            item = NewsItem(
                title=self._clean_summary(entry.get("title", "Untitled")),
                source=source,
                summary=self._clean_summary(entry.get("summary", "")),
                url=link,
                timestamp=timestamp,
            )
            items.append(item)
            cache_items.append({
                "title": item.title,
                "source": item.source,
                "summary": item.summary,
                "url": item.url,
                "timestamp": item.timestamp.isoformat() if item.timestamp else None,
            })

        return items, cache_items

    def fetch_feed(self, url: str) -> list[NewsItem]:
        """
        Fetch a single RSS feed using requests + feedparser.

        Implements the Hybrid Fetcher pattern:
          1. Try the Google Alert RSS feed (curated)
          2. If 0 entries, extract query and fallback to Google News RSS (instant backfill)
        """
        logger.debug(f"Fetching RSS feed: {url[:80]}...")

        # Check cache
        cache = self._load_cache(url)
        if cache and not cache.is_expired(self.cache_ttl_minutes):
            logger.debug(f"Using cached feed ({len(cache.items)} items)")
            return self._items_from_cache(cache)

        etag = cache.etag if cache else None
        modified = cache.last_modified if cache else None

        try:
            response = self._http_get(url, etag=etag, modified=modified)

            # HTTP 304 Not Modified
            if response.status_code == 304 and cache:
                logger.debug("Feed not modified, using cache")
                cache.fetched_at = datetime.now()
                self._save_cache(cache)
                return self._items_from_cache(cache)

            response.raise_for_status()
            feed = self._parse_feed_response(response)

            if feed.bozo and not feed.entries:
                logger.error(f"Feed parse error: {feed.bozo_exception}")
                if cache:
                    return self._items_from_cache(cache)
                return []

            items, cache_items = self._entries_to_items(feed.entries)

            # --- Hybrid Fallback ---
            # If Google Alert feed returned 0 entries, try Google News RSS
            if not items and self._is_google_alert_url(url):
                query = self._extract_query_from_alert_feed(feed)
                if query:
                    logger.info(f"Alert feed empty, falling back to Google News RSS for: {query[:60]}...")
                    fallback_items, fallback_cache = self._fetch_google_news_fallback(query)
                    if fallback_items:
                        items = fallback_items
                        cache_items = fallback_cache
                        logger.info(f"Google News fallback returned {len(items)} items")

            # Update cache
            new_cache = FeedCache(
                url=url,
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                items=cache_items,
                fetched_at=datetime.now(),
            )
            self._save_cache(new_cache)

            logger.debug(f"Fetched {len(items)} items from feed")
            return items

        except requests.RequestException as e:
            logger.error(f"HTTP error fetching feed {url}: {e}")
            if cache:
                logger.debug("Returning cached data due to fetch error")
                return self._items_from_cache(cache)
            return []
        except Exception as e:
            logger.error(f"Error fetching feed {url}: {e}")
            if cache:
                return self._items_from_cache(cache)
            return []

    def _is_google_alert_url(self, url: str) -> bool:
        """Check if a URL is a Google Alerts RSS feed."""
        return "google.com/alerts/feeds/" in url

    def _fetch_google_news_fallback(self, query: str) -> tuple[list[NewsItem], list[dict]]:
        """Fetch from Google News RSS as a fallback for empty Alert feeds."""
        fallback_url = self._build_google_news_url(query)
        try:
            response = self._http_get(fallback_url)
            response.raise_for_status()
            feed = self._parse_feed_response(response)
            if feed.bozo and not feed.entries:
                logger.warning(f"Google News fallback also failed: {feed.bozo_exception}")
                return [], []
            return self._entries_to_items(feed.entries)
        except Exception as e:
            logger.warning(f"Google News fallback error: {e}")
            return [], []

    def _items_from_cache(self, cache: FeedCache) -> list[NewsItem]:
        """Convert cached items to NewsItem objects."""
        items = []
        for item_data in cache.items:
            url = item_data.get("url", "")
            if url and url in self._seen_urls:
                continue
            if url:
                self._seen_urls.add(url)

            timestamp = None
            if item_data.get("timestamp"):
                try:
                    timestamp = datetime.fromisoformat(item_data["timestamp"])
                except Exception:
                    pass

            items.append(NewsItem(
                title=item_data.get("title", "Untitled"),
                source=item_data.get("source", "RSS"),
                summary=item_data.get("summary"),
                url=url,
                timestamp=timestamp,
            ))
        return items

    def _clean_summary(self, summary: str) -> str:
        """Clean HTML and truncate summary."""
        if not summary:
            return ""
        clean = re.sub(r"<[^>]+>", "", summary)
        clean = clean.replace("&nbsp;", " ")
        clean = clean.replace("&amp;", "&")
        clean = clean.replace("&lt;", "<")
        clean = clean.replace("&gt;", ">")
        clean = clean.replace("&quot;", '"')
        clean = re.sub(r"\s+", " ", clean).strip()
        if len(clean) > 500:
            clean = clean[:497] + "..."
        return clean

    def fetch_all_bundled(self, limit: int = 20) -> list["BundledNewsItem"]:
        """
        Fetch all feeds and return with bundle context.

        Uses hybrid fetcher: Google Alert feeds first, Google News RSS fallback if empty.
        """
        from .feed_bundles import BundledNewsItem

        logger.info(f"Fetching {len(self.feed_urls)} RSS feeds from {len(self.bundle_manager.bundles)} bundles...")
        self._seen_urls.clear()

        bundled_items: list[BundledNewsItem] = []

        for bundle in sorted(self.bundle_manager.bundles, key=lambda b: -b.priority):
            for url in bundle.feed_urls:
                items = self.fetch_feed(url)
                for item in items:
                    bundled_items.append(BundledNewsItem(
                        item=item,
                        bundle_name=bundle.name,
                        bundle_arxiv_codes=bundle.arxiv_codes,
                    ))

        # Sort by timestamp (newest first) if available
        bundled_items.sort(
            key=lambda x: x.item.timestamp or datetime.min,
            reverse=True,
        )

        if len(bundled_items) > limit:
            bundled_items = bundled_items[:limit]

        logger.info(f"Total RSS items fetched: {len(bundled_items)}")
        return bundled_items

    def clear_cache(self) -> None:
        """Remove all cached feed data."""
        logger.info("Clearing RSS cache...")
        for cache_file in self.cache_dir.glob("rss_cache_*.json"):
            try:
                cache_file.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete cache file {cache_file}: {e}")
