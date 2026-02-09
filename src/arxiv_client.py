"""arXiv API client for The Agentic Ledger."""

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from openai import OpenAI

from .config import settings
from .utils import get_logger, openai_retry

logger = get_logger(__name__)

# arXiv API base URL
ARXIV_API_URL = "https://export.arxiv.org/api/query"

# XML namespaces for arXiv Atom feed
NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


@dataclass
class ArxivPaper:
    """Represents an arXiv paper."""

    arxiv_id: str
    title: str
    abstract: str
    authors: list[str]
    categories: list[str]
    published: Optional[datetime]
    pdf_url: str
    key_finding: str = ""

    @classmethod
    def placeholder(cls) -> "ArxivPaper":
        """Create a placeholder paper for graceful degradation."""
        return cls(
            arxiv_id="placeholder",
            title="No Relevant Academic Paper Found",
            abstract="Our search did not find a directly relevant academic paper for today's news. "
                     "This segment will focus on general principles and industry analysis instead.",
            authors=["The Agentic Ledger Research Team"],
            categories=["general"],
            published=datetime.now(),
            pdf_url="",
            key_finding="While no specific paper matched today's news, we can draw on established "
                        "research principles in financial AI and quantitative analysis.",
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "arxiv_id": self.arxiv_id,
            "title": self.title,
            "abstract": self.abstract,
            "authors": self.authors,
            "categories": self.categories,
            "published": self.published.isoformat() if self.published else None,
            "pdf_url": self.pdf_url,
            "key_finding": self.key_finding,
        }


class ArxivClient:
    """Client for searching arXiv papers via the Atom API."""

    def __init__(
        self,
        categories: Optional[list[str]] = None,
        max_results: int = 10,
        lookback_days: int = 365,
        openai_client: Optional[OpenAI] = None,
    ):
        """
        Initialize the arXiv client.

        Args:
            categories: arXiv categories to search (e.g., ["cs.CR", "q-fin.TR"])
            max_results: Maximum number of results to fetch
            lookback_days: How far back to search for papers
            openai_client: OpenAI client for key finding extraction
        """
        self.categories = categories or settings.arxiv_categories
        self.max_results = max_results
        self.lookback_days = lookback_days
        self.openai_client = openai_client or OpenAI(api_key=settings.openai_api_key)

    def _build_query(self, keywords: list[str]) -> str:
        """
        Build an arXiv API query string.

        Args:
            keywords: Keywords to search for

        Returns:
            URL-encoded query string
        """
        # Build category filter
        cat_terms = [f"cat:{cat}" for cat in self.categories]
        cat_query = " OR ".join(cat_terms)

        # Build keyword search (search in title and abstract)
        if keywords:
            # Clean and quote multi-word terms
            keyword_terms = []
            for kw in keywords:
                kw = kw.strip().lower()
                if " " in kw:
                    keyword_terms.append(f'"{kw}"')
                else:
                    keyword_terms.append(kw)

            # Search in title (ti) and abstract (abs)
            keyword_query = " OR ".join(keyword_terms)
            search_query = f"(ti:{keyword_query} OR abs:{keyword_query})"
        else:
            search_query = ""

        # Combine queries
        if search_query:
            full_query = f"({cat_query}) AND {search_query}"
        else:
            full_query = f"({cat_query})"

        return full_query

    def search(self, keywords: list[str]) -> Optional[ArxivPaper]:
        """
        Search arXiv for papers matching keywords.

        Args:
            keywords: Keywords extracted from news item

        Returns:
            Best matching ArxivPaper or None if no matches
        """
        if not keywords:
            logger.warning("No keywords provided for arXiv search")
            return None

        logger.info(f"Searching arXiv for: {', '.join(keywords)}")

        query = self._build_query(keywords)
        logger.debug(f"arXiv query: {query}")

        # Build API URL
        params = {
            "search_query": query,
            "start": 0,
            "max_results": self.max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }

        url = f"{ARXIV_API_URL}?{urllib.parse.urlencode(params)}"
        logger.debug(f"arXiv API URL: {url}")

        try:
            # Fetch results
            with urllib.request.urlopen(url, timeout=30) as response:
                xml_data = response.read().decode("utf-8")

            # Parse XML
            papers = self._parse_response(xml_data)

            if not papers:
                logger.info("No papers found matching keywords")
                return None

            # Filter by date (use UTC to match arXiv's timezone-aware dates)
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
            recent_papers = [
                p for p in papers
                if p.published and p.published >= cutoff_date
            ]

            if not recent_papers:
                logger.info(f"No papers within {self.lookback_days} day lookback")
                # Return the most recent one anyway
                if papers:
                    papers.sort(key=lambda x: x.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
                    return papers[0]
                return None

            # Return the most relevant (first) result
            logger.info(f"Found {len(recent_papers)} matching papers, returning best match")
            return recent_papers[0]

        except urllib.error.URLError as e:
            logger.error(f"Failed to fetch from arXiv: {e}")
            return None
        except ET.ParseError as e:
            logger.error(f"Failed to parse arXiv response: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error searching arXiv: {e}")
            return None

    def _parse_response(self, xml_data: str) -> list[ArxivPaper]:
        """
        Parse arXiv Atom XML response.

        Args:
            xml_data: Raw XML response

        Returns:
            List of ArxivPaper objects
        """
        papers = []

        try:
            root = ET.fromstring(xml_data)

            for entry in root.findall("atom:entry", NAMESPACES):
                paper = self._parse_entry(entry)
                if paper:
                    papers.append(paper)

        except ET.ParseError as e:
            logger.error(f"XML parse error: {e}")

        return papers

    def _parse_entry(self, entry: ET.Element) -> Optional[ArxivPaper]:
        """
        Parse a single arXiv entry element.

        Args:
            entry: XML Element for an arXiv entry

        Returns:
            ArxivPaper object or None
        """
        try:
            # Extract ID (format: http://arxiv.org/abs/XXXX.XXXXX)
            id_elem = entry.find("atom:id", NAMESPACES)
            if id_elem is None or id_elem.text is None:
                return None

            arxiv_id = id_elem.text.split("/abs/")[-1]

            # Extract title
            title_elem = entry.find("atom:title", NAMESPACES)
            title = title_elem.text.strip() if title_elem is not None and title_elem.text else "Untitled"
            # Normalize whitespace in title
            title = " ".join(title.split())

            # Extract abstract
            summary_elem = entry.find("atom:summary", NAMESPACES)
            abstract = summary_elem.text.strip() if summary_elem is not None and summary_elem.text else ""
            # Normalize whitespace in abstract
            abstract = " ".join(abstract.split())

            # Extract authors
            authors = []
            for author in entry.findall("atom:author", NAMESPACES):
                name_elem = author.find("atom:name", NAMESPACES)
                if name_elem is not None and name_elem.text:
                    authors.append(name_elem.text.strip())

            # Extract categories
            categories = []
            for cat in entry.findall("atom:category", NAMESPACES):
                term = cat.get("term")
                if term:
                    categories.append(term)

            # Also check arxiv:primary_category
            primary_cat = entry.find("arxiv:primary_category", NAMESPACES)
            if primary_cat is not None:
                term = primary_cat.get("term")
                if term and term not in categories:
                    categories.insert(0, term)

            # Extract published date
            published = None
            published_elem = entry.find("atom:published", NAMESPACES)
            if published_elem is not None and published_elem.text:
                try:
                    published = datetime.fromisoformat(
                        published_elem.text.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            # Extract PDF URL
            pdf_url = ""
            for link in entry.findall("atom:link", NAMESPACES):
                if link.get("title") == "pdf":
                    pdf_url = link.get("href", "")
                    break

            # Fallback: construct PDF URL from ID
            if not pdf_url:
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

            return ArxivPaper(
                arxiv_id=arxiv_id,
                title=title,
                abstract=abstract,
                authors=authors,
                categories=categories,
                published=published,
                pdf_url=pdf_url,
            )

        except Exception as e:
            logger.warning(f"Failed to parse arXiv entry: {e}")
            return None

    @openai_retry
    def extract_key_finding(self, paper: ArxivPaper) -> str:
        """
        Extract the key finding from a paper's abstract using LLM.

        Args:
            paper: ArxivPaper object with abstract

        Returns:
            Key finding summary (1-2 sentences)
        """
        if paper.arxiv_id == "placeholder":
            return paper.key_finding

        logger.info(f"Extracting key finding from: {paper.title[:50]}...")

        prompt = f"""Analyze this academic paper abstract and extract the single most important finding or contribution in 1-2 sentences. Focus on practical implications for financial markets or AI applications.

Title: {paper.title}

Abstract: {paper.abstract}

Key Finding (1-2 sentences, focus on actionable insights):"""

        try:
            response = self.openai_client.chat.completions.create(
                model=settings.llm_model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a research analyst specializing in financial AI. "
                                   "Extract key findings that have practical implications for "
                                   "trading, risk management, or financial technology.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=150,
            )

            finding = response.choices[0].message.content.strip()
            logger.info(f"Extracted key finding: {finding[:100]}...")
            return finding

        except Exception as e:
            logger.error(f"Failed to extract key finding: {e}")
            # Return a generic finding based on the abstract
            return f"This paper presents research on {paper.title.lower()[:50]}..."
