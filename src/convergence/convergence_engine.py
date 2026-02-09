"""Convergence Engine for scoring news items by paper attachment confidence."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from openai import OpenAI

from ..arxiv_client import ArxivClient, ArxivPaper
from ..config import settings
from ..news_ranker import RankedNewsItem
from ..taxonomy import ArxivCategory, ArxivTaxonomyManager
from ..utils import get_logger, openai_retry

logger = get_logger(__name__)


@dataclass
class CategoryMatch:
    """A matched arXiv category with similarity score."""

    category: ArxivCategory
    similarity: float

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "code": self.category.code,
            "name": self.category.name,
            "similarity": self.similarity,
        }


@dataclass
class PaperCandidate:
    """A paper candidate with relevance score."""

    paper: ArxivPaper
    relevance: float
    source_category: str

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "arxiv_id": self.paper.arxiv_id,
            "title": self.paper.title,
            "relevance": self.relevance,
            "source_category": self.source_category,
        }


@dataclass
class ConvergenceResult:
    """Full analysis result for a news item."""

    ranked_item: RankedNewsItem
    categories: list[CategoryMatch]
    papers: list[PaperCandidate]
    convergence_score: float
    combined_score: float
    best_paper: Optional[PaperCandidate] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "news_title": self.ranked_item.item.title,
            "impact_score": self.ranked_item.score,
            "categories": [cat.to_dict() for cat in self.categories],
            "papers": [paper.to_dict() for paper in self.papers],
            "convergence_score": self.convergence_score,
            "combined_score": self.combined_score,
            "best_paper": self.best_paper.to_dict() if self.best_paper else None,
        }


class ConvergenceEngine:
    """Orchestrates the convergence analysis process."""

    def __init__(
        self,
        taxonomy_manager: Optional[ArxivTaxonomyManager] = None,
        arxiv_client: Optional[ArxivClient] = None,
        openai_client: Optional[OpenAI] = None,
        convergence_weight: Optional[float] = None,
    ):
        """
        Initialize the convergence engine.

        Args:
            taxonomy_manager: ArxivTaxonomyManager for category matching
            arxiv_client: ArxivClient for paper search
            openai_client: OpenAI client for embeddings
            convergence_weight: Weight for convergence vs impact score
        """
        self.openai_client = openai_client or OpenAI(api_key=settings.openai_api_key)
        self.taxonomy_manager = taxonomy_manager or ArxivTaxonomyManager(
            openai_client=self.openai_client
        )
        self.arxiv_client = arxiv_client or ArxivClient(
            openai_client=self.openai_client
        )
        self.convergence_weight = convergence_weight or settings.convergence_weight
        self.embedding_model = settings.embedding_model

    def analyze_news_item(
        self,
        ranked_item: RankedNewsItem,
        hint_codes: Optional[list[str]] = None,
    ) -> ConvergenceResult:
        """
        Analyze a single news item for convergence with academic papers.

        Args:
            ranked_item: RankedNewsItem to analyze
            hint_codes: Optional arXiv codes from bundle to boost in matching

        Returns:
            ConvergenceResult with categories, papers, and scores
        """
        item = ranked_item.item
        logger.info(f"Analyzing: {item.title[:50]}...")

        # Step 1: Find matching categories (with optional bundle hints)
        if hint_codes:
            categories = self._find_categories_with_hints(ranked_item, hint_codes)
        else:
            categories = self._find_categories_for_news(ranked_item)
        logger.debug(f"Found {len(categories)} matching categories")

        # Step 2: Search papers for matched categories
        papers = self._search_papers_for_categories(ranked_item, categories)
        logger.debug(f"Found {len(papers)} paper candidates")

        # Step 3: Calculate convergence score
        convergence_score = self._calculate_convergence_score(categories, papers)

        # Step 4: Calculate combined score
        impact_normalized = ranked_item.score / 10.0  # Normalize to 0-1
        combined_score = (
            (1 - self.convergence_weight) * impact_normalized
            + self.convergence_weight * convergence_score
        )

        # Find best paper
        best_paper = papers[0] if papers else None

        result = ConvergenceResult(
            ranked_item=ranked_item,
            categories=categories,
            papers=papers,
            convergence_score=convergence_score,
            combined_score=combined_score,
            best_paper=best_paper,
        )

        logger.info(
            f"Convergence: {convergence_score:.3f}, "
            f"Combined: {combined_score:.3f}, "
            f"Papers: {len(papers)}"
        )

        return result

    def select_best_story(
        self,
        ranked_items: list[RankedNewsItem],
    ) -> tuple[ConvergenceResult, list[ConvergenceResult]]:
        """
        Select the best story based on combined impact x convergence score.

        Args:
            ranked_items: List of RankedNewsItem objects to analyze

        Returns:
            Tuple of (best_result, all_results)
        """
        if not ranked_items:
            raise ValueError("No ranked items provided")

        logger.info(f"Analyzing {len(ranked_items)} news items for convergence...")

        # Analyze each news item
        results = []
        for ranked_item in ranked_items:
            result = self.analyze_news_item(ranked_item)
            results.append(result)

        # Sort by combined score (descending)
        results.sort(key=lambda x: x.combined_score, reverse=True)

        best = results[0]
        logger.info(
            f"Best story: {best.ranked_item.item.title[:50]}... "
            f"(combined={best.combined_score:.3f})"
        )

        # Save results to temp directory
        self._save_results(best, results)

        return best, results

    def _find_categories_for_news(
        self,
        ranked_item: RankedNewsItem,
    ) -> list[CategoryMatch]:
        """Find matching arXiv categories for a news item."""
        item = ranked_item.item

        # Combine title and summary for embedding
        text = item.title
        if item.summary:
            text = f"{text}\n\n{item.summary}"

        # Find matching categories
        matches = self.taxonomy_manager.find_matching_categories(
            text=text,
            top_k=settings.convergence_categories_per_news,
            min_similarity=settings.convergence_min_similarity,
        )

        return [
            CategoryMatch(category=cat, similarity=score)
            for cat, score in matches
        ]

    def _find_categories_with_hints(
        self,
        ranked_item: RankedNewsItem,
        hint_codes: list[str],
    ) -> list[CategoryMatch]:
        """
        Find categories with boost for bundle hints.

        Categories matching hint_codes get a +0.15 similarity boost.
        """
        base_matches = self._find_categories_for_news(ranked_item)

        # Apply boost to hinted categories
        boosted = []
        for match in base_matches:
            boost = 0.15 if match.category.code in hint_codes else 0.0
            boosted.append(CategoryMatch(
                category=match.category,
                similarity=min(1.0, match.similarity + boost),
            ))

        # Re-sort after boosting
        boosted.sort(key=lambda x: x.similarity, reverse=True)
        return boosted[:settings.convergence_categories_per_news]

    def _search_papers_for_categories(
        self,
        ranked_item: RankedNewsItem,
        categories: list[CategoryMatch],
    ) -> list[PaperCandidate]:
        """Search for papers in the matched categories."""
        if not categories:
            return []

        item = ranked_item.item
        papers: list[PaperCandidate] = []
        seen_ids: set[str] = set()

        # Extract keywords from news for paper search
        keywords = self._extract_search_keywords(item.title, item.summary)

        for cat_match in categories:
            category_code = cat_match.category.code

            # Create a category-specific arxiv client
            arxiv_client = ArxivClient(
                categories=[category_code],
                max_results=settings.convergence_papers_per_category,
                lookback_days=settings.arxiv_lookback_days,
                openai_client=self.openai_client,
            )

            # Search for papers
            paper = arxiv_client.search(keywords)

            if paper and paper.arxiv_id not in seen_ids:
                seen_ids.add(paper.arxiv_id)

                # Score paper relevance
                relevance = self._score_paper_relevance(ranked_item, paper)

                if relevance >= settings.convergence_min_relevance:
                    papers.append(PaperCandidate(
                        paper=paper,
                        relevance=relevance,
                        source_category=category_code,
                    ))

        # Sort by relevance (descending)
        papers.sort(key=lambda x: x.relevance, reverse=True)

        return papers

    def _extract_search_keywords(
        self,
        title: str,
        summary: Optional[str],
    ) -> list[str]:
        """Extract search keywords from news title and summary."""
        # Combine title and summary
        text = title
        if summary:
            text = f"{text} {summary}"

        # Simple keyword extraction: split on spaces, filter short words
        words = text.lower().split()
        stopwords = {
            "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
            "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
            "been", "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "must", "shall", "can",
            "this", "that", "these", "those", "it", "its", "they", "them",
        }

        keywords = []
        for word in words:
            # Clean punctuation
            clean_word = "".join(c for c in word if c.isalnum())
            if len(clean_word) > 3 and clean_word not in stopwords:
                keywords.append(clean_word)

        # Deduplicate while preserving order
        seen = set()
        unique_keywords = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                unique_keywords.append(kw)

        # Return top 5 keywords
        return unique_keywords[:5]

    @openai_retry
    def _score_paper_relevance(
        self,
        ranked_item: RankedNewsItem,
        paper: ArxivPaper,
    ) -> float:
        """
        Score paper relevance to news item using embedding similarity.

        Args:
            ranked_item: The news item
            paper: The paper to score

        Returns:
            Relevance score between 0 and 1
        """
        item = ranked_item.item

        # Create text representations
        news_text = item.title
        if item.summary:
            news_text = f"{news_text}\n{item.summary}"

        paper_text = f"{paper.title}\n{paper.abstract}"

        try:
            # Get embeddings for both texts
            response = self.openai_client.embeddings.create(
                model=self.embedding_model,
                input=[news_text, paper_text],
            )

            news_embedding = np.array(response.data[0].embedding)
            paper_embedding = np.array(response.data[1].embedding)

            # Calculate cosine similarity
            similarity = self._cosine_similarity(news_embedding, paper_embedding)

            return similarity

        except Exception as e:
            logger.warning(f"Failed to score paper relevance: {e}")
            return 0.5  # Default middle score

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Calculate cosine similarity between two vectors."""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    def _calculate_convergence_score(
        self,
        categories: list[CategoryMatch],
        papers: list[PaperCandidate],
    ) -> float:
        """
        Calculate the convergence score for a news item.

        Formula:
            convergence = 0.4 * best_category_similarity
                        + 0.4 * best_paper_relevance
                        + 0.2 * candidate_diversity

        Args:
            categories: List of matched categories
            papers: List of paper candidates

        Returns:
            Convergence score between 0 and 1
        """
        # Best category similarity
        best_cat_sim = categories[0].similarity if categories else 0.0

        # Best paper relevance
        best_paper_rel = papers[0].relevance if papers else 0.0

        # Candidate diversity: how many unique categories have papers
        if papers:
            unique_cats = len(set(p.source_category for p in papers))
            max_cats = min(len(categories), settings.convergence_categories_per_news)
            diversity = unique_cats / max_cats if max_cats > 0 else 0.0
        else:
            diversity = 0.0

        # Weighted combination
        convergence = (
            0.4 * best_cat_sim
            + 0.4 * best_paper_rel
            + 0.2 * diversity
        )

        return convergence

    def _save_results(
        self,
        best: ConvergenceResult,
        all_results: list[ConvergenceResult],
    ) -> None:
        """Save convergence results to temp directory."""
        results_file = settings.temp_dir / "convergence_results.json"
        settings.temp_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "selected": best.to_dict(),
            "all_candidates": [r.to_dict() for r in all_results],
            "convergence_weight": self.convergence_weight,
        }

        results_file.write_text(json.dumps(data, indent=2))
        logger.info(f"Saved convergence results to: {results_file}")
