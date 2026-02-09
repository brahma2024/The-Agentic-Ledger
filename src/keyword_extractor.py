"""Keyword extraction for arXiv paper matching in The Agentic Ledger."""

import json
from typing import Optional

from openai import OpenAI

from .config import settings
from .scraper import NewsItem
from .utils import get_logger, openai_retry

logger = get_logger(__name__)


EXTRACTION_SYSTEM_PROMPT = """You are a research keyword extraction specialist for The Agentic Ledger, a podcast bridging financial news and academic research.

Your task is to extract academic keywords from news items that can be used to search arXiv for relevant papers.

EXTRACTION GUIDELINES:
1. Focus on technical/academic terms, not proper nouns or company names
2. Include methodology terms (e.g., "reinforcement learning", "time series analysis")
3. Include domain terms (e.g., "market microstructure", "portfolio optimization")
4. Prefer terms likely to appear in academic paper titles and abstracts
5. Avoid overly generic terms (e.g., "technology", "data")

GOOD KEYWORD EXAMPLES:
- "large language models" (not "ChatGPT")
- "algorithmic trading" (not "trading app")
- "zero-knowledge proofs" (not "ZK rollup startup")
- "sentiment analysis" (not "social media")
- "anomaly detection" (not "fraud prevention")

OUTPUT FORMAT (JSON):
{
  "keywords": ["keyword1", "keyword2", "keyword3"],
  "reasoning": "Brief explanation of why these keywords were chosen"
}
"""


class KeywordExtractor:
    """Extracts academic keywords from news items for arXiv matching."""

    def __init__(self, client: Optional[OpenAI] = None):
        """
        Initialize the keyword extractor.

        Args:
            client: OpenAI client instance (creates one if not provided)
        """
        self.client = client or OpenAI(api_key=settings.openai_api_key)

    @openai_retry
    def extract(self, item: NewsItem, max_keywords: int = 5) -> list[str]:
        """
        Extract academic keywords from a news item.

        Args:
            item: NewsItem to extract keywords from
            max_keywords: Maximum number of keywords to return

        Returns:
            List of keyword strings for arXiv search
        """
        logger.info(f"Extracting keywords from: {item.title[:50]}...")

        # Build the prompt with the news item
        user_prompt = f"""Extract {max_keywords} academic research keywords from this news item that could match relevant arXiv papers.

Title: {item.title}

Summary: {item.summary or 'No summary available'}

Source: {item.source}

Focus on:
- Technical concepts and methodologies
- Academic research areas
- Domain-specific terminology

Return JSON with keywords and brief reasoning."""

        try:
            response = self.client.chat.completions.create(
                model=settings.llm_model,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=300,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            keywords = self._parse_keywords(content, max_keywords)

            if keywords:
                logger.info(f"Extracted keywords: {keywords}")
            else:
                logger.warning("No keywords extracted, using fallback")
                keywords = self._fallback_keywords(item)

            return keywords

        except Exception as e:
            logger.error(f"Failed to extract keywords: {e}")
            return self._fallback_keywords(item)

    def _parse_keywords(self, content: str, max_keywords: int) -> list[str]:
        """Parse LLM response to extract keyword list."""
        try:
            data = json.loads(content)

            # Handle different response formats
            if isinstance(data, dict):
                keywords = data.get("keywords", [])
            elif isinstance(data, list):
                keywords = data
            else:
                keywords = []

            # Clean and validate keywords
            cleaned = []
            for kw in keywords:
                if isinstance(kw, str):
                    kw = kw.strip().lower()
                    # Skip very short or very long keywords
                    if 2 < len(kw) < 50:
                        cleaned.append(kw)

            return cleaned[:max_keywords]

        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to parse keywords response: {e}")
            return []

    def _fallback_keywords(self, item: NewsItem) -> list[str]:
        """Generate fallback keywords from the news item."""
        # Common financial AI keywords
        base_keywords = ["machine learning", "financial markets"]

        # Try to extract some terms from the title
        title_words = item.title.lower().split()

        # Common domain terms to look for
        domain_terms = [
            "ai", "ml", "trading", "crypto", "blockchain", "llm",
            "neural", "network", "algorithm", "quantitative", "risk",
            "model", "deep learning", "nlp", "sentiment", "prediction",
        ]

        for term in domain_terms:
            if term in title_words:
                base_keywords.append(term)

        return base_keywords[:5]

    def extract_batch(
        self,
        items: list[NewsItem],
        keywords_per_item: int = 3,
    ) -> list[str]:
        """
        Extract keywords from multiple news items and deduplicate.

        Args:
            items: List of NewsItem objects
            keywords_per_item: Keywords to extract from each item

        Returns:
            Deduplicated list of keywords
        """
        all_keywords: list[str] = []
        seen: set[str] = set()

        for item in items:
            keywords = self.extract(item, max_keywords=keywords_per_item)
            for kw in keywords:
                if kw.lower() not in seen:
                    seen.add(kw.lower())
                    all_keywords.append(kw)

        return all_keywords
