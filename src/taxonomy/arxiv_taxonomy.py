"""arXiv category taxonomy manager with cached embeddings."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
from openai import OpenAI

from ..config import settings
from ..utils import get_logger, openai_retry

logger = get_logger(__name__)


@dataclass
class ArxivCategory:
    """Represents an arXiv category with its embedding."""

    code: str
    name: str
    description: str
    embedding: Optional[list[float]] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "embedding": self.embedding,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ArxivCategory":
        """Create from dictionary."""
        return cls(
            code=data["code"],
            name=data["name"],
            description=data["description"],
            embedding=data.get("embedding"),
        )


@dataclass
class TaxonomyCache:
    """Cache container with TTL expiration for taxonomy data."""

    categories: list[ArxivCategory]
    created_at: datetime
    ttl_days: int

    def is_valid(self) -> bool:
        """Check if the cache is still valid."""
        expiry = self.created_at + timedelta(days=self.ttl_days)
        return datetime.now() < expiry

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "categories": [cat.to_dict() for cat in self.categories],
            "created_at": self.created_at.isoformat(),
            "ttl_days": self.ttl_days,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaxonomyCache":
        """Create from dictionary."""
        return cls(
            categories=[ArxivCategory.from_dict(c) for c in data["categories"]],
            created_at=datetime.fromisoformat(data["created_at"]),
            ttl_days=data["ttl_days"],
        )


# Hardcoded arXiv categories relevant for finance/AI intersection
ARXIV_CATEGORIES = [
    # Computer Science - AI/ML
    ArxivCategory(
        code="cs.AI",
        name="Artificial Intelligence",
        description="Artificial intelligence, reasoning, problem solving, knowledge representation, "
                   "planning, machine learning, multi-agent systems, intelligent robotics.",
    ),
    ArxivCategory(
        code="cs.LG",
        name="Machine Learning",
        description="Machine learning algorithms, supervised learning, unsupervised learning, "
                   "reinforcement learning, deep learning, neural networks, statistical learning.",
    ),
    ArxivCategory(
        code="cs.CR",
        name="Cryptography and Security",
        description="Cryptography, security protocols, blockchain, distributed ledger technology, "
                   "smart contracts, privacy, authentication, cybersecurity.",
    ),
    ArxivCategory(
        code="cs.CL",
        name="Computation and Language",
        description="Natural language processing, text analysis, sentiment analysis, language models, "
                   "large language models, transformers, NLP applications in finance.",
    ),
    ArxivCategory(
        code="cs.NE",
        name="Neural and Evolutionary Computing",
        description="Neural networks, evolutionary algorithms, genetic algorithms, neuroevolution, "
                   "optimization, metaheuristics for trading strategies.",
    ),
    ArxivCategory(
        code="cs.IR",
        name="Information Retrieval",
        description="Information retrieval, search engines, recommendation systems, "
                   "document classification, text mining for financial data.",
    ),
    ArxivCategory(
        code="cs.GT",
        name="Computer Science and Game Theory",
        description="Game theory, mechanism design, auctions, market design, algorithmic game theory, "
                   "multi-agent systems, strategic decision making.",
    ),
    ArxivCategory(
        code="cs.MA",
        name="Multiagent Systems",
        description="Multi-agent systems, distributed AI, agent-based simulation, "
                   "market simulation, collective intelligence, swarm intelligence.",
    ),
    ArxivCategory(
        code="cs.DC",
        name="Distributed Computing",
        description="Distributed computing, parallel processing, consensus protocols, "
                   "distributed ledgers, high-frequency trading infrastructure.",
    ),
    # Quantitative Finance
    ArxivCategory(
        code="q-fin.TR",
        name="Trading and Market Microstructure",
        description="Trading strategies, market microstructure, order book dynamics, "
                   "algorithmic trading, high-frequency trading, execution algorithms.",
    ),
    ArxivCategory(
        code="q-fin.PM",
        name="Portfolio Management",
        description="Portfolio optimization, asset allocation, risk-return tradeoff, "
                   "factor investing, robo-advisors, wealth management.",
    ),
    ArxivCategory(
        code="q-fin.RM",
        name="Risk Management",
        description="Risk management, value at risk, stress testing, market risk, "
                   "credit risk, operational risk, systemic risk.",
    ),
    ArxivCategory(
        code="q-fin.CP",
        name="Computational Finance",
        description="Computational finance, numerical methods, Monte Carlo simulation, "
                   "finite difference methods, option pricing, derivatives.",
    ),
    ArxivCategory(
        code="q-fin.MF",
        name="Mathematical Finance",
        description="Mathematical finance, stochastic calculus, derivative pricing, "
                   "continuous-time finance, martingale theory.",
    ),
    ArxivCategory(
        code="q-fin.ST",
        name="Statistical Finance",
        description="Statistical finance, financial econometrics, time series analysis, "
                   "volatility modeling, market prediction, quantitative analysis.",
    ),
    ArxivCategory(
        code="q-fin.GN",
        name="General Finance",
        description="General finance topics, financial markets, behavioral finance, "
                   "market efficiency, fintech, regulatory developments.",
    ),
    ArxivCategory(
        code="q-fin.EC",
        name="Economics",
        description="Financial economics, market dynamics, price formation, "
                   "information economics, mechanism design in markets.",
    ),
    # Statistics
    ArxivCategory(
        code="stat.ML",
        name="Machine Learning (Statistics)",
        description="Statistical machine learning, probabilistic models, Bayesian methods, "
                   "statistical inference for prediction and forecasting.",
    ),
    ArxivCategory(
        code="stat.ME",
        name="Methodology",
        description="Statistical methodology, hypothesis testing, model selection, "
                   "time series methods, causal inference.",
    ),
    ArxivCategory(
        code="stat.AP",
        name="Applications",
        description="Statistical applications, applied statistics, data analysis, "
                   "empirical studies in finance and economics.",
    ),
    # Economics
    ArxivCategory(
        code="econ.EM",
        name="Econometrics",
        description="Econometrics, economic forecasting, causal inference, "
                   "panel data, time series econometrics, machine learning in economics.",
    ),
    ArxivCategory(
        code="econ.TH",
        name="Theoretical Economics",
        description="Economic theory, game theory, mechanism design, "
                   "market design, auction theory, information economics.",
    ),
    ArxivCategory(
        code="econ.GN",
        name="General Economics",
        description="General economics, macroeconomics, monetary policy, "
                   "central banking, financial regulation, policy analysis.",
    ),
    # Mathematics
    ArxivCategory(
        code="math.OC",
        name="Optimization and Control",
        description="Optimization theory, control theory, dynamic programming, "
                   "convex optimization, stochastic control for trading.",
    ),
    ArxivCategory(
        code="math.PR",
        name="Probability",
        description="Probability theory, stochastic processes, martingales, "
                   "random processes in finance, limit theorems.",
    ),
    ArxivCategory(
        code="math.ST",
        name="Statistics Theory",
        description="Statistical theory, estimation, testing, asymptotic theory, "
                   "mathematical foundations of machine learning.",
    ),
]


class ArxivTaxonomyManager:
    """Manages arXiv category taxonomy with cached embeddings."""

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        ttl_days: Optional[int] = None,
        openai_client: Optional[OpenAI] = None,
        embedding_model: Optional[str] = None,
    ):
        """
        Initialize the taxonomy manager.

        Args:
            cache_dir: Directory for caching taxonomy embeddings
            ttl_days: Time-to-live for cache in days
            openai_client: OpenAI client for generating embeddings
            embedding_model: Model to use for embeddings
        """
        self.cache_dir = cache_dir or settings.temp_dir
        self.ttl_days = ttl_days or settings.convergence_cache_ttl_days
        self.openai_client = openai_client or OpenAI(api_key=settings.openai_api_key)
        self.embedding_model = embedding_model or settings.embedding_model

        self._categories: Optional[list[ArxivCategory]] = None
        self._cache_path = self._get_cache_path()

    def _get_cache_path(self) -> Path:
        """Generate cache file path based on category codes."""
        codes = sorted(cat.code for cat in ARXIV_CATEGORIES)
        hash_input = f"{self.embedding_model}:{','.join(codes)}"
        cache_hash = hashlib.md5(hash_input.encode()).hexdigest()[:12]
        return self.cache_dir / f"arxiv_taxonomy_{cache_hash}.json"

    def load_taxonomy(self) -> list[ArxivCategory]:
        """
        Load taxonomy with embeddings (from cache or generate new).

        Returns:
            List of ArxivCategory objects with embeddings
        """
        if self._categories is not None:
            return self._categories

        # Try loading from cache
        cached = self._load_cache()
        if cached and cached.is_valid():
            logger.info(f"Loaded taxonomy from cache: {self._cache_path}")
            self._categories = cached.categories
            return self._categories

        # Generate embeddings for all categories
        logger.info("Generating embeddings for taxonomy categories...")
        self._categories = self._generate_embeddings(ARXIV_CATEGORIES.copy())

        # Save to cache
        self._save_cache(self._categories)
        logger.info(f"Saved taxonomy cache to: {self._cache_path}")

        return self._categories

    def _load_cache(self) -> Optional[TaxonomyCache]:
        """Load taxonomy cache from disk."""
        if not self._cache_path.exists():
            return None

        try:
            data = json.loads(self._cache_path.read_text())
            return TaxonomyCache.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to load taxonomy cache: {e}")
            return None

    def _save_cache(self, categories: list[ArxivCategory]) -> None:
        """Save taxonomy cache to disk."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache = TaxonomyCache(
            categories=categories,
            created_at=datetime.now(),
            ttl_days=self.ttl_days,
        )
        self._cache_path.write_text(json.dumps(cache.to_dict(), indent=2))

    @openai_retry
    def _generate_embeddings(self, categories: list[ArxivCategory]) -> list[ArxivCategory]:
        """Generate embeddings for all categories in a single batch."""
        # Prepare texts for embedding
        texts = [
            f"{cat.name}: {cat.description}"
            for cat in categories
        ]

        try:
            response = self.openai_client.embeddings.create(
                model=self.embedding_model,
                input=texts,
            )

            # Assign embeddings to categories
            for i, cat in enumerate(categories):
                cat.embedding = response.data[i].embedding

            logger.info(f"Generated embeddings for {len(categories)} categories")
            return categories

        except Exception as e:
            logger.error(f"Failed to generate embeddings: {e}")
            # Return categories without embeddings as fallback
            return categories

    @openai_retry
    def get_embedding(self, text: str) -> Optional[list[float]]:
        """
        Get embedding for a single text.

        Args:
            text: Text to embed

        Returns:
            Embedding vector or None on failure
        """
        try:
            response = self.openai_client.embeddings.create(
                model=self.embedding_model,
                input=text,
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Failed to get embedding: {e}")
            return None

    def find_matching_categories(
        self,
        text: str,
        top_k: int = 5,
        min_similarity: Optional[float] = None,
    ) -> list[tuple[ArxivCategory, float]]:
        """
        Find categories matching the given text using semantic search.

        Args:
            text: Text to match against categories
            top_k: Number of top matches to return
            min_similarity: Minimum similarity threshold

        Returns:
            List of (category, similarity_score) tuples, sorted by score
        """
        min_similarity = min_similarity or settings.convergence_min_similarity
        categories = self.load_taxonomy()

        # Get embedding for the input text
        text_embedding = self.get_embedding(text)
        if text_embedding is None:
            logger.warning("Failed to embed text, falling back to default categories")
            return self._fallback_categories(top_k)

        text_embedding_np = np.array(text_embedding)

        # Calculate cosine similarity with each category
        similarities = []
        for cat in categories:
            if cat.embedding is None:
                continue

            cat_embedding = np.array(cat.embedding)
            similarity = self._cosine_similarity(text_embedding_np, cat_embedding)
            similarities.append((cat, similarity))

        # Sort by similarity (descending)
        similarities.sort(key=lambda x: x[1], reverse=True)

        # Filter by minimum similarity and take top K
        results = [
            (cat, score) for cat, score in similarities[:top_k]
            if score >= min_similarity
        ]

        if not results:
            logger.warning(f"No categories matched above threshold {min_similarity}")
            # Return top matches anyway if nothing meets threshold
            return similarities[:top_k]

        logger.debug(f"Found {len(results)} matching categories for text")
        return results

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Calculate cosine similarity between two vectors."""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    def _fallback_categories(self, top_k: int) -> list[tuple[ArxivCategory, float]]:
        """Return default categories when embedding fails."""
        default_codes = ["cs.AI", "cs.LG", "q-fin.TR", "cs.CR", "q-fin.RM"]
        categories = self.load_taxonomy()
        results = []
        for cat in categories:
            if cat.code in default_codes:
                results.append((cat, 0.5))  # Default similarity
        return results[:top_k]

    def get_category_codes(self, categories: list[tuple[ArxivCategory, float]]) -> list[str]:
        """Extract category codes from category-score tuples."""
        return [cat.code for cat, _ in categories]
