"""Category-aligned RSS feed bundles for upstream convergence."""

from dataclasses import dataclass, field
from typing import Optional

from ..scraper import NewsItem
from ..utils import get_logger

logger = get_logger(__name__)


@dataclass
class RSSFeedBundle:
    """A collection of RSS feeds linked to arXiv categories."""

    name: str
    feed_urls: list[str]
    arxiv_codes: list[str]
    description: Optional[str] = None
    priority: int = 0
    enabled: bool = True

    def to_dict(self) -> dict:
        """Serialize for JSON storage."""
        return {
            "name": self.name,
            "feed_urls": self.feed_urls,
            "arxiv_codes": self.arxiv_codes,
            "description": self.description,
            "priority": self.priority,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RSSFeedBundle":
        """Deserialize from JSON."""
        return cls(
            name=data["name"],
            feed_urls=data.get("feed_urls", []),
            arxiv_codes=data.get("arxiv_codes", []),
            description=data.get("description"),
            priority=data.get("priority", 0),
            enabled=data.get("enabled", True),
        )


@dataclass
class BundledNewsItem:
    """NewsItem with bundle context for convergence hints."""

    item: NewsItem
    bundle_name: str
    bundle_arxiv_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "item": self.item.to_dict(),
            "bundle_name": self.bundle_name,
            "bundle_arxiv_codes": self.bundle_arxiv_codes,
        }


class BundleManager:
    """Manages RSS feed bundles with category alignment."""

    def __init__(self, bundles: list[RSSFeedBundle]):
        """
        Initialize the bundle manager.

        Args:
            bundles: List of RSSFeedBundle objects
        """
        self.bundles = [b for b in bundles if b.enabled]
        self._code_to_bundles: dict[str, list[RSSFeedBundle]] = {}
        self._build_index()

    def _build_index(self) -> None:
        """Build reverse index from arxiv code to bundles."""
        for bundle in self.bundles:
            for code in bundle.arxiv_codes:
                if code not in self._code_to_bundles:
                    self._code_to_bundles[code] = []
                self._code_to_bundles[code].append(bundle)

    def get_bundles_for_category(self, arxiv_code: str) -> list[RSSFeedBundle]:
        """Get all bundles aligned with an arXiv category."""
        return self._code_to_bundles.get(arxiv_code, [])

    def get_all_feed_urls(self) -> list[str]:
        """Get flat list of all feed URLs, prioritized by bundle priority."""
        urls = []
        seen = set()
        for bundle in sorted(self.bundles, key=lambda b: -b.priority):
            for url in bundle.feed_urls:
                if url not in seen:
                    urls.append(url)
                    seen.add(url)
        return urls

    def get_bundle_for_url(self, url: str) -> Optional[RSSFeedBundle]:
        """Find which bundle a URL belongs to."""
        for bundle in self.bundles:
            if url in bundle.feed_urls:
                return bundle
        return None

    @classmethod
    def from_settings(cls, settings) -> "BundleManager":
        """Create BundleManager from settings."""
        bundles_data = settings.parsed_feed_bundles
        if not bundles_data:
            logger.warning("No RSS_FEED_BUNDLES configured")
            return cls([])

        bundles = [RSSFeedBundle.from_dict(d) for d in bundles_data]
        enabled_count = sum(1 for b in bundles if b.enabled)
        logger.info(f"Loaded {enabled_count} RSS feed bundles")
        return cls(bundles)
