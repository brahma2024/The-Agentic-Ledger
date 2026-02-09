"""LLM-based news ranking by financial impact for The Agentic Ledger."""

import json
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI

from .config import settings
from .scraper import NewsItem
from .utils import get_logger, openai_retry

logger = get_logger(__name__)


@dataclass
class RankedNewsItem:
    """A news item with its financial impact score and reasoning."""

    item: NewsItem
    score: float  # 1-10 scale
    reasoning: str

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "item": self.item.to_dict(),
            "score": self.score,
            "reasoning": self.reasoning,
        }


RANKING_SYSTEM_PROMPT = """You are a financial news analyst for The Agentic Ledger, a podcast that bridges cutting-edge AI/finance NEWS with ACADEMIC RESEARCH.

Your task is to score news items by their STRUCTURAL SIGNIFICANCE on a scale of 1-10.

## CRITICAL FILTERING RULES

AUTOMATICALLY SCORE 1-2 (DISCARD):
- "Business Gossip": CEO hired/fired, stock price movements, earnings reports, layoffs
- "Vague Hype": "AI will change everything", "blockchain is the future", opinion pieces
- "Product Marketing": Company announces new feature, partnership announcements without technical detail
- "Personality News": What Elon said, what Sam Altman thinks

SCORE 3-5 (LOW PRIORITY):
- Funding rounds without technical innovation
- General industry trends without specific mechanisms
- Regulatory discussions without concrete policy changes

SCORE 6-8 (HIGH PRIORITY):
- New protocols or standards being adopted
- Specific algorithmic improvements with measurable results
- Security vulnerabilities with technical details (CVEs, exploits)
- Regulatory ACTIONS (not discussions) affecting market structure

SCORE 9-10 (CRITICAL - PRIORITIZE):
- New algorithms or methods with published benchmarks
- Protocol-level changes (consensus mechanisms, trading infrastructure)
- Zero-day exploits or novel attack vectors
- Quantitative research findings with reproducible results
- Market microstructure changes (new exchange rules, order types)

## THE KEY QUESTION
Ask yourself: "Could an academic paper be written about the MECHANISM described in this news?"
- YES → Score 6-10
- NO → Score 1-5

## FOCUS AREAS (must involve STRUCTURAL/TECHNICAL content):
- AI/ML algorithms and architectures (not just "company uses AI")
- Cryptographic protocols and security mechanisms
- Trading algorithms and market microstructure
- Distributed systems and consensus protocols
- Optimization methods and quantitative techniques

For each news item, provide:
1. A score (1-10)
2. Brief reasoning focusing on WHY it's structural or not

OUTPUT FORMAT (JSON array):
[
  {"index": 0, "score": 9.0, "reasoning": "New consensus protocol with 40% latency improvement - structural"},
  {"index": 1, "score": 2.0, "reasoning": "CEO commentary on AI future - business gossip, no technical content"}
]
"""


class NewsRanker:
    """Ranks news items by financial impact using LLM analysis."""

    def __init__(self, client: Optional[OpenAI] = None):
        """
        Initialize the news ranker.

        Args:
            client: OpenAI client instance (creates one if not provided)
        """
        self.client = client or OpenAI(api_key=settings.openai_api_key)

    @openai_retry
    def rank_by_financial_impact(
        self,
        items: list[NewsItem],
        top_n: int = 5,
    ) -> list[RankedNewsItem]:
        """
        Rank news items by their financial impact.

        Args:
            items: List of NewsItem objects to rank
            top_n: Number of top items to return

        Returns:
            List of RankedNewsItem objects, sorted by score (highest first)
        """
        if not items:
            logger.warning("No items to rank")
            return []

        logger.info(f"Ranking {len(items)} news items by financial impact...")

        # Format news items for the prompt
        news_list = self._format_items_for_prompt(items)

        user_prompt = f"""Analyze and score these news items by financial market impact (1-10 scale):

{news_list}

Return a JSON array with scores and brief reasoning for each item. Consider:
- Direct market impact
- Implications for AI/finance intersection
- Regulatory significance
- Innovation potential"""

        try:
            response = self.client.chat.completions.create(
                model=settings.llm_model,
                messages=[
                    {"role": "system", "content": RANKING_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,  # More deterministic for ranking
                max_tokens=2000,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            rankings = self._parse_rankings(content, items)

            # Sort by score (highest first)
            rankings.sort(key=lambda x: x.score, reverse=True)

            # Return top N
            top_items = rankings[:top_n]

            logger.info(f"Top {len(top_items)} ranked items:")
            for i, ranked in enumerate(top_items, 1):
                logger.info(f"  {i}. [{ranked.score:.1f}] {ranked.item.title[:50]}...")

            return top_items

        except Exception as e:
            logger.error(f"Failed to rank news items: {e}")
            # Fallback: return items in original order with default scores
            return self._fallback_ranking(items, top_n)

    def _format_items_for_prompt(self, items: list[NewsItem]) -> str:
        """Format news items as a numbered list for the prompt."""
        lines = []
        for i, item in enumerate(items):
            line = f"{i}. [{item.source}] {item.title}"
            if item.summary:
                # Truncate long summaries
                summary = item.summary[:200] + "..." if len(item.summary) > 200 else item.summary
                line += f"\n   {summary}"
            lines.append(line)
        return "\n\n".join(lines)

    def _parse_rankings(
        self,
        content: str,
        items: list[NewsItem],
    ) -> list[RankedNewsItem]:
        """Parse LLM response into RankedNewsItem objects."""
        rankings = []

        try:
            data = json.loads(content)

            # Handle both direct array and object with "rankings" key
            if isinstance(data, list):
                ranking_list = data
            elif isinstance(data, dict):
                ranking_list = data.get("rankings", data.get("items", data.get("scores", [])))
            else:
                raise ValueError(f"Unexpected response format: {type(data)}")

            for entry in ranking_list:
                idx = entry.get("index", 0)
                score = float(entry.get("score", 5.0))
                reasoning = entry.get("reasoning", "No reasoning provided")

                # Clamp score to valid range
                score = max(1.0, min(10.0, score))

                if 0 <= idx < len(items):
                    rankings.append(RankedNewsItem(
                        item=items[idx],
                        score=score,
                        reasoning=reasoning,
                    ))

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Failed to parse rankings: {e}")
            # Try to extract scores from text if JSON parsing fails
            rankings = self._fallback_ranking(items, len(items))

        # Ensure all items have a ranking
        ranked_indices = {r.item.url or r.item.title for r in rankings}
        for item in items:
            key = item.url or item.title
            if key not in ranked_indices:
                rankings.append(RankedNewsItem(
                    item=item,
                    score=5.0,  # Default middle score
                    reasoning="Unable to parse ranking for this item",
                ))

        return rankings

    def _fallback_ranking(
        self,
        items: list[NewsItem],
        top_n: int,
    ) -> list[RankedNewsItem]:
        """Create fallback rankings when LLM ranking fails."""
        logger.warning("Using fallback ranking (original order)")
        rankings = []
        for i, item in enumerate(items[:top_n]):
            rankings.append(RankedNewsItem(
                item=item,
                score=7.0 - (i * 0.5),  # Decreasing scores
                reasoning="Ranked by recency (fallback)",
            ))
        return rankings

    def get_top_item(self, items: list[NewsItem]) -> Optional[RankedNewsItem]:
        """
        Get the single highest-ranked news item.

        Args:
            items: List of NewsItem objects

        Returns:
            Highest-ranked RankedNewsItem or None
        """
        ranked = self.rank_by_financial_impact(items, top_n=1)
        return ranked[0] if ranked else None
