"""Category lexicon generator for news-friendly search phrases."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
from openai import OpenAI

from ..config import settings
from ..utils import get_logger, openai_retry
from .arxiv_taxonomy import ArxivCategory, ArxivTaxonomyManager

logger = get_logger(__name__)


LEXICON_SYSTEM_PROMPT = """You are an expert at creating search phrases that find NEWS about STRUCTURAL/TECHNICAL changes in a field.

Given an arXiv category, generate 15-25 search phrases that will find news about:
- New algorithms, protocols, or methods
- Security vulnerabilities with technical details (CVEs, exploits)
- Benchmark results and performance improvements
- Infrastructure and architectural changes
- Research breakthroughs with reproducible results

AVOID phrases that would match:
- Business gossip (CEO changes, funding rounds, stock prices)
- Vague hype ("AI revolution", "blockchain future")
- Product marketing without technical substance
- Opinion pieces and predictions

The goal is to find news that could be connected to ACADEMIC RESEARCH - news about mechanisms, not personalities.

PHRASE CATEGORIES TO INCLUDE:
1. PROTOCOL/ALGORITHM phrases: "new consensus mechanism", "attention architecture"
2. VULNERABILITY/EXPLOIT phrases: "CVE-", "zero-day", "buffer overflow"
3. BENCHMARK/PERFORMANCE phrases: "benchmark results", "latency improvement"
4. INFRASTRUCTURE phrases: "protocol upgrade", "hard fork"
5. RESEARCH phrases: "paper published", "peer reviewed"

Examples for cs.CR (Cryptography and Security):
- "CVE disclosure"
- "zero-day exploit"
- "post-quantum migration"
- "side-channel attack"
- "formal verification"
- "protocol vulnerability"
- "cryptographic primitive"

Examples for cs.AI (Artificial Intelligence):
- "transformer architecture"
- "reinforcement learning benchmark"
- "reasoning capability"
- "emergent behavior"
- "chain of thought"
- "inference optimization"

OUTPUT FORMAT (JSON):
{
  "phrases": [
    "phrase one",
    "phrase two",
    ...
  ]
}
"""


@dataclass
class LexiconPhrase:
    """A news-friendly phrase derived from an arXiv category."""

    phrase: str
    confidence: float
    category_code: str

    def to_dict(self) -> dict:
        """Serialize for caching."""
        return {
            "phrase": self.phrase,
            "confidence": self.confidence,
            "category_code": self.category_code,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LexiconPhrase":
        """Deserialize from cache."""
        return cls(
            phrase=data["phrase"],
            confidence=data["confidence"],
            category_code=data["category_code"],
        )


@dataclass
class CategoryLexicon:
    """News-friendly lexicon for an arXiv category."""

    category_code: str
    category_name: str
    phrases: list[LexiconPhrase]
    generated_at: datetime

    def to_dict(self) -> dict:
        """Serialize for caching."""
        return {
            "category_code": self.category_code,
            "category_name": self.category_name,
            "phrases": [p.to_dict() for p in self.phrases],
            "generated_at": self.generated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CategoryLexicon":
        """Deserialize from cache."""
        return cls(
            category_code=data["category_code"],
            category_name=data["category_name"],
            phrases=[LexiconPhrase.from_dict(p) for p in data["phrases"]],
            generated_at=datetime.fromisoformat(data["generated_at"]),
        )

    def get_high_confidence_phrases(self, min_confidence: float = 0.5) -> list[str]:
        """Return phrases above confidence threshold."""
        return [p.phrase for p in self.phrases if p.confidence >= min_confidence]

    def get_top_phrases(self, top_k: int = 10) -> list[str]:
        """Get top K phrases by confidence."""
        sorted_phrases = sorted(self.phrases, key=lambda p: p.confidence, reverse=True)
        return [p.phrase for p in sorted_phrases[:top_k]]


@dataclass
class LexiconCache:
    """Cache container for all category lexicons."""

    lexicons: dict[str, CategoryLexicon]
    created_at: datetime
    ttl_days: int

    def is_valid(self) -> bool:
        """Check if cache is still valid."""
        expiry = self.created_at + timedelta(days=self.ttl_days)
        return datetime.now() < expiry

    def to_dict(self) -> dict:
        """Serialize for caching."""
        return {
            "lexicons": {code: lex.to_dict() for code, lex in self.lexicons.items()},
            "created_at": self.created_at.isoformat(),
            "ttl_days": self.ttl_days,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LexiconCache":
        """Deserialize from cache."""
        return cls(
            lexicons={
                code: CategoryLexicon.from_dict(lex_data)
                for code, lex_data in data["lexicons"].items()
            },
            created_at=datetime.fromisoformat(data["created_at"]),
            ttl_days=data["ttl_days"],
        )


class CategoryLexiconGenerator:
    """Generates and caches news-friendly lexicons for arXiv categories."""

    def __init__(
        self,
        taxonomy_manager: Optional[ArxivTaxonomyManager] = None,
        cache_dir: Optional[Path] = None,
        ttl_days: int = 30,
        openai_client: Optional[OpenAI] = None,
    ):
        """
        Initialize the lexicon generator.

        Args:
            taxonomy_manager: ArxivTaxonomyManager for category data
            cache_dir: Directory to store cache files
            ttl_days: Cache time-to-live in days
            openai_client: OpenAI client for LLM calls
        """
        self.taxonomy_manager = taxonomy_manager or ArxivTaxonomyManager()
        self.cache_dir = cache_dir or settings.temp_dir
        self.ttl_days = ttl_days
        self.openai_client = openai_client or OpenAI(api_key=settings.openai_api_key)
        self._cache_path = self.cache_dir / "category_lexicons.json"
        self._lexicons: Optional[dict[str, CategoryLexicon]] = None

    def get_lexicon(self, category_code: str) -> Optional[CategoryLexicon]:
        """Get lexicon for a specific category (generates if needed)."""
        self._ensure_loaded()
        return self._lexicons.get(category_code)

    def get_all_lexicons(self) -> dict[str, CategoryLexicon]:
        """Get all category lexicons."""
        self._ensure_loaded()
        return self._lexicons

    def _ensure_loaded(self) -> None:
        """Load from cache or generate all lexicons."""
        if self._lexicons is not None:
            return

        # Try loading from cache
        cached = self._load_cache()
        if cached and cached.is_valid():
            logger.info(f"Loaded lexicon cache: {len(cached.lexicons)} categories")
            self._lexicons = cached.lexicons
            return

        # Generate all lexicons
        logger.info("Generating category lexicons (this may take a minute)...")
        self._lexicons = self._generate_all_lexicons()
        self._save_cache()

    @openai_retry
    def _generate_lexicon_for_category(
        self,
        category: ArxivCategory,
    ) -> CategoryLexicon:
        """Generate lexicon for a single category using LLM."""
        user_prompt = f"""Generate news-friendly search phrases for this arXiv category:

Category Code: {category.code}
Category Name: {category.name}
Academic Description: {category.description}

Generate 15-25 phrases that would appear in mainstream tech/business/finance news.
Focus on current terminology, product names, company types, and event types.
"""

        response = self.openai_client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": LEXICON_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=1000,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        data = json.loads(content)
        phrases = data.get("phrases", [])

        # Score each phrase by semantic similarity to category
        scored_phrases = self._score_phrases(category, phrases)

        return CategoryLexicon(
            category_code=category.code,
            category_name=category.name,
            phrases=scored_phrases,
            generated_at=datetime.now(),
        )

    def _score_phrases(
        self,
        category: ArxivCategory,
        phrases: list[str],
    ) -> list[LexiconPhrase]:
        """Score phrases by semantic similarity to category."""
        if not phrases:
            return []

        if category.embedding is None:
            logger.warning(f"No embedding for {category.code}, using default scores")
            return [
                LexiconPhrase(phrase=p, confidence=0.5, category_code=category.code)
                for p in phrases
            ]

        try:
            # Get embeddings for all phrases in batch
            response = self.openai_client.embeddings.create(
                model=settings.embedding_model,
                input=phrases,
            )

            phrase_embeddings = [d.embedding for d in response.data]
            category_embedding = np.array(category.embedding)

            scored = []
            for phrase, emb in zip(phrases, phrase_embeddings):
                similarity = self._cosine_similarity(
                    np.array(emb),
                    category_embedding
                )
                scored.append(LexiconPhrase(
                    phrase=phrase,
                    confidence=float(similarity),
                    category_code=category.code,
                ))

            # Sort by confidence descending
            scored.sort(key=lambda x: x.confidence, reverse=True)
            return scored

        except Exception as e:
            logger.warning(f"Failed to score phrases: {e}")
            return [
                LexiconPhrase(phrase=p, confidence=0.5, category_code=category.code)
                for p in phrases
            ]

    def _generate_all_lexicons(self) -> dict[str, CategoryLexicon]:
        """Generate lexicons for all taxonomy categories."""
        categories = self.taxonomy_manager.load_taxonomy()
        lexicons = {}

        total = len(categories)
        for i, cat in enumerate(categories, 1):
            try:
                logger.info(f"[{i}/{total}] Generating lexicon for {cat.code}: {cat.name}")
                lexicon = self._generate_lexicon_for_category(cat)
                lexicons[cat.code] = lexicon
                logger.debug(f"Generated {len(lexicon.phrases)} phrases for {cat.code}")
            except Exception as e:
                logger.error(f"Failed to generate lexicon for {cat.code}: {e}")

        return lexicons

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Calculate cosine similarity."""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    def _load_cache(self) -> Optional[LexiconCache]:
        """Load lexicon cache from disk."""
        if not self._cache_path.exists():
            return None
        try:
            data = json.loads(self._cache_path.read_text())
            return LexiconCache.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load lexicon cache: {e}")
            return None

    def _save_cache(self) -> None:
        """Save lexicons to cache."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache = LexiconCache(
            lexicons=self._lexicons,
            created_at=datetime.now(),
            ttl_days=self.ttl_days,
        )
        self._cache_path.write_text(json.dumps(cache.to_dict(), indent=2))
        logger.info(f"Saved lexicon cache to: {self._cache_path}")

    def export_for_google_alerts(
        self,
        category_code: str,
        max_phrases: int = 10,
    ) -> list[str]:
        """Export phrases formatted for Google Alerts queries."""
        lexicon = self.get_lexicon(category_code)
        if not lexicon:
            return []

        top_phrases = lexicon.get_top_phrases(max_phrases)
        return [f'"{phrase}"' for phrase in top_phrases]

    def get_combined_alert_query(
        self,
        category_codes: list[str],
        phrases_per_category: int = 5,
    ) -> str:
        """Generate a combined Google Alerts query for multiple categories."""
        all_phrases = []
        for code in category_codes:
            phrases = self.export_for_google_alerts(code, phrases_per_category)
            all_phrases.extend(phrases)

        if not all_phrases:
            return ""

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for p in all_phrases:
            if p not in seen:
                seen.add(p)
                unique.append(p)

        return " OR ".join(unique[:15])

    def refresh_category(self, category_code: str) -> Optional[CategoryLexicon]:
        """Force refresh lexicon for a specific category."""
        categories = self.taxonomy_manager.load_taxonomy()
        category = next((c for c in categories if c.code == category_code), None)

        if not category:
            logger.error(f"Unknown category: {category_code}")
            return None

        lexicon = self._generate_lexicon_for_category(category)
        if self._lexicons is None:
            self._lexicons = {}
        self._lexicons[category_code] = lexicon
        self._save_cache()
        return lexicon
