"""Scene planner for visual storyboard system.

Generates a timeline of 9 scene cards from script metadata,
using one cheap LLM call (gpt-4o-mini) to extract visual copy.
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from openai import OpenAI

from .arxiv_client import ArxivPaper
from .config import settings
from .convergence.convergence_engine import ConvergenceResult
from .scraper import NewsItem
from .utils import get_logger, openai_retry

logger = get_logger(__name__)


class CardType(Enum):
    """Types of visual scene cards."""

    TITLE = "TITLE"
    CONTEXT = "CONTEXT"
    HEADLINE = "HEADLINE"
    KEY_STAT = "KEY_STAT"
    BRIDGE = "BRIDGE"
    PAPER = "PAPER"
    QUOTE = "QUOTE"
    ALPHA = "ALPHA"
    SUMMARY = "SUMMARY"


# Proportional timing for each card (fraction of total duration)
SCENE_TIMING = [
    (CardType.TITLE, 0.00, 0.06),
    (CardType.CONTEXT, 0.06, 0.12),
    (CardType.HEADLINE, 0.12, 0.30),
    (CardType.KEY_STAT, 0.30, 0.37),
    (CardType.BRIDGE, 0.37, 0.44),
    (CardType.PAPER, 0.44, 0.65),
    (CardType.QUOTE, 0.65, 0.73),
    (CardType.ALPHA, 0.73, 0.92),
    (CardType.SUMMARY, 0.92, 1.00),
]


@dataclass
class VisualCopy:
    """LLM-extracted text for scene cards."""

    episode_topic: str
    headline_bullets: list[str]
    bridge_insight: str
    alpha_bullets: list[str]
    key_takeaway: str
    # New fields for expanded card types
    context_stat: str = ""
    context_explanation: str = ""
    key_number: str = ""
    key_number_context: str = ""
    key_quote: str = ""
    quote_attribution: str = ""
    news_one_liner: str = ""


@dataclass
class SceneCard:
    """A single scene in the visual timeline."""

    card_type: CardType
    start_s: float
    end_s: float
    content: dict

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass
class SceneTimeline:
    """Complete visual timeline for an episode."""

    cards: list[SceneCard]
    audio_duration_s: float
    visual_copy: VisualCopy


class ScenePlanner:
    """Plans scene cards from script + metadata using one LLM call."""

    def __init__(self, openai_client: Optional[OpenAI] = None):
        self.openai_client = openai_client or OpenAI(api_key=settings.openai_api_key)

    def plan(
        self,
        script: str,
        top_news: NewsItem,
        paper: ArxivPaper,
        audio_duration_s: float,
        convergence_result: Optional[ConvergenceResult] = None,
    ) -> SceneTimeline:
        """
        Plan a visual storyboard from script and metadata.

        Args:
            script: Generated podcast script text
            top_news: The top news item used in the episode
            paper: The arXiv paper used in the episode
            audio_duration_s: Duration of audio in seconds
            convergence_result: Optional convergence analysis result

        Returns:
            SceneTimeline with 9 scene cards
        """
        logger.info("Planning visual storyboard...")

        # Extract visual copy via LLM (with fallback)
        try:
            visual_copy = self._extract_visual_copy(script)
        except Exception as e:
            logger.warning(f"LLM visual copy extraction failed: {e}, using fallback")
            visual_copy = self._fallback_visual_copy(top_news, paper)

        # Build timeline from visual copy + metadata
        cards = self._build_timeline(visual_copy, top_news, paper, audio_duration_s)

        timeline = SceneTimeline(
            cards=cards,
            audio_duration_s=audio_duration_s,
            visual_copy=visual_copy,
        )

        logger.info(f"Storyboard planned: {len(cards)} scenes over {audio_duration_s:.1f}s")
        return timeline

    @openai_retry
    def _extract_visual_copy(self, script: str) -> VisualCopy:
        """Extract visual copy from script using gpt-4o-mini."""
        logger.info("Extracting visual copy via LLM...")

        prompt = f"""Analyze this podcast script and extract visual copy for on-screen cards.

SCRIPT:
{script[:3000]}

Return JSON with exactly these fields:
- "episode_topic": A concise 5-8 word topic line (e.g., "AI Trading Bots Beat Wall Street")
- "headline_bullets": Array of exactly 3 key points from the news segment (each 8-15 words)
- "bridge_insight": One sentence explaining the hidden mechanism connecting the news to the research (15-25 words)
- "alpha_bullets": Array of exactly 3 actionable takeaways (each 8-15 words, start with a verb)
- "key_takeaway": One memorable sentence summarizing the episode (10-20 words)
- "context_stat": A single dramatic statistic from the story (e.g., "47% of hedge funds now use AI")
- "context_explanation": Why that statistic matters (10-20 words)
- "key_number": A single impactful number from the research (e.g., "$4.2B", "0.87", "3.2x")
- "key_number_context": What that number represents (8-15 words)
- "key_quote": One impactful sentence from the discussion worth highlighting (15-25 words)
- "quote_attribution": Who said it or where it comes from (e.g., "QUANT", "Fed Research")
- "news_one_liner": Single sentence summarizing the news story (10-15 words)"""

        response = self.openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You extract concise visual copy for podcast video cards. "
                    "Return only valid JSON. Be specific and actionable.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=800,
        )

        data = json.loads(response.choices[0].message.content)

        # Validate and coerce
        headline_bullets = data.get("headline_bullets", [])[:3]
        while len(headline_bullets) < 3:
            headline_bullets.append("Key insight from today's story")

        alpha_bullets = data.get("alpha_bullets", [])[:3]
        while len(alpha_bullets) < 3:
            alpha_bullets.append("Monitor developments in this space")

        return VisualCopy(
            episode_topic=data.get("episode_topic", "Today's Episode")[:80],
            headline_bullets=headline_bullets,
            bridge_insight=data.get("bridge_insight", "Research reveals hidden patterns in market behavior")[:200],
            alpha_bullets=alpha_bullets,
            key_takeaway=data.get("key_takeaway", "Stay informed, stay ahead")[:150],
            context_stat=data.get("context_stat", "")[:100],
            context_explanation=data.get("context_explanation", "")[:200],
            key_number=data.get("key_number", "")[:20],
            key_number_context=data.get("key_number_context", "")[:100],
            key_quote=data.get("key_quote", "")[:200],
            quote_attribution=data.get("quote_attribution", "")[:60],
            news_one_liner=data.get("news_one_liner", "")[:100],
        )

    def _fallback_visual_copy(self, top_news: NewsItem, paper: ArxivPaper) -> VisualCopy:
        """Build visual copy from raw metadata without LLM."""
        logger.info("Using fallback visual copy from metadata")

        # Extract topic from news title
        topic = top_news.title[:80] if top_news.title else "Today's Episode"

        # Build bullets from summary
        summary = top_news.summary or ""
        sentences = [s.strip() for s in summary.replace("\n", ". ").split(".") if s.strip()]
        headline_bullets = sentences[:3]
        while len(headline_bullets) < 3:
            headline_bullets.append("Breaking developments in financial AI")

        # Build bridge from paper finding
        bridge = paper.key_finding[:200] if paper.key_finding else "Research connects market patterns to underlying mechanisms"

        # Alpha bullets from paper abstract
        abstract_sentences = [s.strip() for s in paper.abstract.split(".") if s.strip()][:3]
        alpha_bullets = [f"Apply: {s[:60]}" for s in abstract_sentences]
        while len(alpha_bullets) < 3:
            alpha_bullets.append("Monitor this research area for developments")

        # Fallback values for new fields
        context_stat = sentences[0][:100] if sentences else "Emerging trend in financial AI"
        key_finding = paper.key_finding or ""
        key_quote = key_finding[:200] if key_finding else "The data reveals patterns previously hidden from traditional analysis"

        return VisualCopy(
            episode_topic=topic,
            headline_bullets=headline_bullets,
            bridge_insight=bridge,
            alpha_bullets=alpha_bullets,
            key_takeaway=f"The connection between {top_news.title[:30]}... and academic research reveals actionable insights",
            context_stat=context_stat,
            context_explanation="This signals a shift in how markets process information",
            key_number="0.87",
            key_number_context="Correlation coefficient between key variables",
            key_quote=key_quote,
            quote_attribution="QUANT",
            news_one_liner=top_news.title[:100] if top_news.title else "Breaking developments today",
        )

    def _build_timeline(
        self,
        visual_copy: VisualCopy,
        top_news: NewsItem,
        paper: ArxivPaper,
        audio_duration_s: float,
    ) -> list[SceneCard]:
        """Build scene cards with proportional timing."""
        cards = []
        today = __import__("datetime").date.today().strftime("%B %d, %Y")

        for card_type, start_frac, end_frac in SCENE_TIMING:
            start_s = audio_duration_s * start_frac
            end_s = audio_duration_s * end_frac

            if card_type == CardType.TITLE:
                content = {
                    "brand": settings.podcast_name,
                    "topic": visual_copy.episode_topic,
                    "date": today,
                }
            elif card_type == CardType.CONTEXT:
                content = {
                    "section_label": "WHY THIS MATTERS",
                    "stat": visual_copy.context_stat or "Emerging trend",
                    "explanation": visual_copy.context_explanation or "A shift in how markets operate",
                    "one_liner": visual_copy.news_one_liner or top_news.title[:80],
                }
            elif card_type == CardType.HEADLINE:
                content = {
                    "section_label": "THE NEWS",
                    "title": top_news.title,
                    "source": top_news.source,
                    "bullets": visual_copy.headline_bullets,
                }
            elif card_type == CardType.KEY_STAT:
                content = {
                    "number": visual_copy.key_number or "N/A",
                    "context": visual_copy.key_number_context or "Key metric from the research",
                }
            elif card_type == CardType.BRIDGE:
                content = {
                    "section_label": "THE CONNECTION",
                    "news_title": top_news.title,
                    "bridge_insight": visual_copy.bridge_insight,
                    "playbook_hint": visual_copy.alpha_bullets[0] if visual_copy.alpha_bullets else "",
                }
            elif card_type == CardType.PAPER:
                # Abbreviate authors: "Smith, J. et al."
                authors = paper.authors[:3]
                if len(paper.authors) > 3:
                    author_str = ", ".join(authors[:2]) + " et al."
                else:
                    author_str = ", ".join(authors)

                content = {
                    "section_label": "THE PAPER",
                    "title": paper.title,
                    "authors": author_str,
                    "key_finding": paper.key_finding or "See abstract for details",
                    "arxiv_id": paper.arxiv_id,
                }
            elif card_type == CardType.QUOTE:
                content = {
                    "quote": visual_copy.key_quote or visual_copy.key_takeaway,
                    "attribution": visual_copy.quote_attribution or "THE AGENTIC LEDGER",
                }
            elif card_type == CardType.ALPHA:
                content = {
                    "section_label": "THE ALPHA",
                    "header": "ACTIONABLE INSIGHTS",
                    "bullets": visual_copy.alpha_bullets,
                }
            elif card_type == CardType.SUMMARY:
                content = {
                    "brand": settings.podcast_name,
                    "topic": visual_copy.episode_topic,
                    "key_takeaway": visual_copy.key_takeaway,
                }

            cards.append(SceneCard(
                card_type=card_type,
                start_s=start_s,
                end_s=end_s,
                content=content,
            ))

        return cards
