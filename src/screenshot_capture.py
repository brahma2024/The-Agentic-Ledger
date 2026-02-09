"""Webpage screenshot capture using Playwright for video backgrounds."""

import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .config import settings
from .utils import get_logger

logger = get_logger(__name__)

# Allowed URL schemes for screenshot capture (SSRF protection)
_ALLOWED_SCHEMES = {"https"}

# Blocked IP ranges (private, loopback, link-local, metadata endpoints)
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # AWS/GCP metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_safe_url(url: str) -> bool:
    """Validate that a URL is safe to fetch (SSRF protection).

    Restricts to HTTPS scheme and blocks private/internal IP ranges.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in _ALLOWED_SCHEMES:
        logger.warning(f"Blocked URL with disallowed scheme: {parsed.scheme}")
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    try:
        resolved_ips = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        logger.warning(f"DNS resolution failed for: {hostname}")
        return False

    for _family, _type, _proto, _canonname, sockaddr in resolved_ips:
        ip = ipaddress.ip_address(sockaddr[0])
        for network in _BLOCKED_NETWORKS:
            if ip in network:
                logger.warning(f"Blocked URL resolving to private IP: {hostname} -> {ip}")
                return False

    return True


@dataclass
class ScreenshotPair:
    """Pair of screenshots for video background."""

    news_screenshot: Optional[Path] = None
    paper_screenshot: Optional[Path] = None


class ScreenshotCapture:
    """Captures webpage screenshots using Playwright headless Chromium."""

    def __init__(self, viewport_width: int = 1920, viewport_height: int = 1080):
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height

    def capture_webpage(self, url: str, output_path: Path, timeout_ms: int = 30000) -> bool:
        """
        Capture a full-page screenshot of a URL.

        Args:
            url: URL to capture (must be HTTPS, non-private IP)
            output_path: Path to save the PNG screenshot
            timeout_ms: Page load timeout in milliseconds

        Returns:
            True if capture succeeded, False otherwise
        """
        if not _is_safe_url(url):
            logger.error(f"URL blocked by SSRF protection: {url}")
            return False

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
            return False

        logger.info(f"Capturing screenshot: {url}")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(
                    viewport={"width": self.viewport_width, "height": self.viewport_height},
                )
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                page.screenshot(path=str(output_path), full_page=False)
                browser.close()

            logger.info(f"Screenshot saved: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Screenshot capture failed for {url}: {e}")
            return False

    def capture_news_and_paper(
        self,
        news_url: Optional[str],
        arxiv_id: Optional[str],
    ) -> ScreenshotPair:
        """
        Capture screenshots of a news article and an arXiv paper.

        Args:
            news_url: URL of the news article
            arxiv_id: arXiv paper ID (e.g. "2401.12345")

        Returns:
            ScreenshotPair with paths to captured screenshots (None for failures)
        """
        pair = ScreenshotPair()
        temp_dir = settings.temp_dir
        temp_dir.mkdir(parents=True, exist_ok=True)

        if news_url:
            news_path = temp_dir / "news_screenshot.png"
            if self.capture_webpage(news_url, news_path):
                pair.news_screenshot = news_path

        if arxiv_id:
            paper_url = f"https://arxiv.org/abs/{arxiv_id}"
            paper_path = temp_dir / "paper_screenshot.png"
            if self.capture_webpage(paper_url, paper_path):
                pair.paper_screenshot = paper_path

        return pair
