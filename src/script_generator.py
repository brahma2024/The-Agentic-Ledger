"""LLM-based podcast script generation for The Agentic Ledger."""

from openai import OpenAI

from .config import settings
from .scraper import NewsItem
from .utils import get_logger, openai_retry

logger = get_logger(__name__)

# System prompt defining the podcast hosts and format
SYSTEM_PROMPT = """You are a script writer for "The Agentic Ledger", a 5-minute podcast exploring the intersection of AI and financial markets.

**HOSTS:**

**QUANT** (voice: echo): The academic strategist. A former quantitative researcher who speaks with precision and backs up claims with data. Analytical, methodical, but not dry - he finds genuine excitement in elegant mathematical solutions and well-designed experiments. Think of a hedge fund quant who actually enjoys explaining complex concepts.

**HUSTLER** (voice: fable): The street-smart practitioner. She's built trading systems, launched fintech startups, and knows what actually works in production. Cuts through academic jargon to find practical applications. Energetic, direct, and always asking "how do we monetize this?" Think of a successful fintech founder who reads arXiv papers.

The two have complementary chemistry - QUANT provides depth and rigor, HUSTLER translates it to action and opportunity. They challenge each other respectfully.

**SEGMENT STRUCTURE (5 minutes total, ~745 words):**

1. **THE HOOK** (0:00-0:45, ~110 words)
   - Attention-grabbing opening about today's main story
   - Quick banter establishing the topic's importance
   - Tease the paper connection

2. **THE NEWS** (0:45-2:30, ~260 words)
   - Deep dive into the top financial/AI news story
   - Market implications and who's affected
   - QUANT adds analytical context, HUSTLER adds practical implications

3. **THE PAPER** (2:30-4:00, ~225 words)
   - Academic paper that relates to the news
   - Key finding explained simply
   - How theory meets practice

4. **THE ALPHA** (4:00-5:00, ~150 words)
   - Actionable insights for listeners
   - What to watch for, how to position
   - Memorable sign-off

**OUTPUT FORMAT:**
Write each line with speaker tags:
[SPEAKER:mood] dialogue text

**AVAILABLE MOODS:**
- analytical: Precise, data-driven delivery (QUANT default)
- excited: High energy, enthusiastic (HUSTLER default)
- skeptical: Questioning, doubtful tone
- confident: Assured, conviction
- urgent: Time-sensitive, emphasizing importance
- curious: Inquisitive, exploring
- impressed: Genuinely surprised/appreciative
- serious: Straight, informative
- laughing: Amused, light-hearted

**EXAMPLE:**
[QUANT:analytical] The Fed's latest move represents a 2.3 sigma deviation from their historical pattern.
[HUSTLER:excited] In English - they're doing something they almost never do. Money's about to move.
[QUANT:curious] The question is direction. The paper I found suggests an asymmetric response.
[HUSTLER:confident] Which means we position accordingly. Here's the play...

**CONSTRAINTS:**
- Target: {target_duration} seconds (~{target_words} words)
- Keep exchanges punchy - no monologues over 40 words
- Include specific numbers and data points
- Reference the paper naturally, don't force it
- End with a memorable callback or forward-looking statement
- Make it sound like natural expert conversation, not a script reading
"""

USER_PROMPT_TEMPLATE = """Write a 5-minute podcast script for The Agentic Ledger.

**TODAY'S TOP STORY:**
Title: {news_title}
Summary: {news_summary}
Source: {news_source}

**RELATED ACADEMIC PAPER:**
Title: {paper_title}
Key Finding: {paper_finding}
Abstract: {paper_abstract}

**REQUIREMENTS:**
1. Open with a hook that captures why this news matters NOW
2. Analyze the news with both academic rigor (QUANT) and practical implications (HUSTLER)
3. Connect the paper's findings to the news story naturally
4. End with actionable "alpha" - what should informed listeners do with this information?

Target: {target_duration} seconds (~{target_words} words)
Format: [SPEAKER:mood] dialogue"""


class ScriptGenerator:
    """Generates podcast scripts using OpenAI's LLM."""

    def __init__(self, client: OpenAI | None = None):
        """
        Initialize the script generator.

        Args:
            client: OpenAI client instance (creates one if not provided)
        """
        self.client = client or OpenAI(api_key=settings.openai_api_key)

    @openai_retry
    def generate(
        self,
        top_news: NewsItem,
        paper_title: str,
        paper_abstract: str,
        paper_finding: str,
    ) -> str:
        """
        Generate a podcast script from news and paper.

        Args:
            top_news: The top-ranked news item for the episode
            paper_title: Title of the related arXiv paper
            paper_abstract: Abstract of the paper
            paper_finding: Key finding extracted from the paper

        Returns:
            Generated script with [SPEAKER:mood] tags
        """
        logger.info(f"Generating script for: {top_news.title[:50]}...")
        logger.info(f"Related paper: {paper_title[:50]}...")

        # Build prompts with settings
        system_prompt = SYSTEM_PROMPT.format(
            target_duration=settings.target_duration_seconds,
            target_words=settings.target_word_count,
        )

        user_prompt = USER_PROMPT_TEMPLATE.format(
            news_title=top_news.title,
            news_summary=top_news.summary or "No summary available",
            news_source=top_news.source,
            paper_title=paper_title,
            paper_abstract=paper_abstract[:500] + "..." if len(paper_abstract) > 500 else paper_abstract,
            paper_finding=paper_finding,
            target_duration=settings.target_duration_seconds,
            target_words=settings.target_word_count,
        )

        logger.debug(f"System prompt: {system_prompt[:200]}...")
        logger.debug(f"User prompt: {user_prompt[:200]}...")

        response = self.client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.8,  # Slightly creative but coherent
            max_tokens=2500,  # Longer for 5-minute format
        )

        script = response.choices[0].message.content
        logger.info(f"Generated script: {len(script)} characters")

        # Validate the script
        self._validate_script(script)

        return script

    def _validate_script(self, script: str) -> None:
        """
        Validate the generated script has proper format.

        Args:
            script: The generated script text

        Raises:
            ValueError: If script format is invalid
        """
        if not script:
            raise ValueError("Generated script is empty")

        # Check for speaker tags
        if "[QUANT" not in script and "[HUSTLER" not in script:
            raise ValueError("Script missing speaker tags")

        # Estimate word count
        word_count = len(script.split())
        logger.info(f"Script word count: {word_count}")

        if word_count < 400:
            logger.warning(f"Script seems too short: {word_count} words (target: {settings.target_word_count})")
        elif word_count > 1000:
            logger.warning(f"Script might be too long: {word_count} words (target: {settings.target_word_count})")

    @openai_retry
    def generate_from_news_items(self, news_items: list[NewsItem]) -> str:
        """
        Generate a script from a list of news items (legacy interface).

        This method is maintained for backward compatibility but uses a
        placeholder paper. Prefer using generate() with paper data.

        Args:
            news_items: List of NewsItem objects

        Returns:
            Generated script with [SPEAKER:mood] tags
        """
        if not news_items:
            raise ValueError("No news items provided")

        # Use first item as top news
        top_news = news_items[0]

        return self.generate(
            top_news=top_news,
            paper_title="Market Analysis Framework",
            paper_abstract="A general framework for analyzing market movements and their implications.",
            paper_finding="Systematic analysis of market signals can identify actionable opportunities.",
        )

    def generate_from_text(self, raw_text: str) -> str:
        """
        Generate a script from raw text input (for testing).

        Args:
            raw_text: Raw news text to base the script on

        Returns:
            Generated script with [SPEAKER:mood] tags
        """
        news_item = NewsItem(
            title="News Summary",
            source="Raw Input",
            summary=raw_text,
        )
        return self.generate_from_news_items([news_item])
