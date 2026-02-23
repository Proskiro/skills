"""
Book ranking logic with source-aware scoring.
"""

import math
from datetime import datetime
from typing import Dict, List, Tuple

# Trusted publishers for professional/technical books
TRUSTED_PUBLISHERS = {
    "o'reilly",
    "oreilly",
    "wiley",
    "springer",
    "pearson",
    "manning",
    "packt",
    "apress",
    "mit press",
    "mcgraw-hill",
    "mcgraw hill",
    "addison-wesley",
    "addison wesley",
    "pragmatic",
    "no starch",
    "cambridge university press",
    "oxford university press",
    "harvard business",
    "portfolio",
    "penguin business",
    "hbr",
    "kogan page",
}


class BookRanker:
    """
    Source-aware book ranking with configurable weights.
    """

    def __init__(self, source: str = "google_books"):
        """
        Initialize the ranker for a specific source.

        Args:
            source: The book source (google_books, open_library)
        """
        self.source = source
        self.weights = self._get_weights()

    def _get_weights(self) -> Dict:
        """Get merged weights for the current source."""
        # Default weights
        weights = {
            "relevance_order": 40,  # Bonus for search result position
            "recency": 20,  # Max bonus for recent publications
            "rating_count_cap": 100,  # Max rating count contribution
            "rating_multiplier": 10,  # average_rating * this
            "publisher": 10,
            "trusted_publisher": 25,  # Bonus for reputable publishers
            "subjects": 5,
            "educational_subject": 30,  # Bonus for educational/textbook subjects
        }

        # Source-specific overrides
        source_weights = {
            "google_books": {
                "relevance_order": 40,
            },
            "open_library": {
                "relevance_order": 10,
                "popularity": 30,
                "edition_count": 15,
                "subject_match": 25,
            },
        }

        weights.update(source_weights.get(self.source, {}))
        return weights

    def score(self, idx: int, book: Dict) -> float:
        """Calculate ranking score for a book."""
        s = 0.0

        # Search position relevance (earlier = better)
        s += max(0, self.weights["relevance_order"] - idx)

        # Recency boost (age-based)
        year = book.get("published_year")
        if year:
            age = datetime.utcnow().year - year
            s += max(0, self.weights["recency"] - age)

        # Rating boost (only if significant)
        ratings_count = book.get("ratings_count") or 0
        average_rating = book.get("average_rating") or 0

        if ratings_count >= 10:
            s += min(ratings_count, self.weights["rating_count_cap"])
            s += average_rating * self.weights["rating_multiplier"]

        # Metadata quality
        publisher = book.get("publisher", "")
        if publisher:
            s += self.weights["publisher"]
            # Trusted publisher bonus
            if any(trusted in publisher.lower() for trusted in TRUSTED_PUBLISHERS):
                s += self.weights["trusted_publisher"]
        if book.get("subjects"):
            s += self.weights["subjects"]

        # Educational subject boost - prefer textbooks/professional books
        s += self._score_educational_subjects(book)

        # Source-specific scoring
        if self.source == "open_library":
            s += self._score_open_library(book)

        # Semantic relevance score (if present)
        relevance = book.get("semantic_relevance_score", 0) or 0
        s += relevance * 50  # Weight for semantic relevance

        return s

    def _score_educational_subjects(self, book: Dict) -> float:
        """
        Boost books with educational/professional subject indicators.
        """
        educational_indicators = {
            "textbook",
            "textbooks",
            "education",
            "teaching",
            "learning",
            "curriculum",
            "academic",
            "professional",
            "handbook",
            "guide",
            "manual",
            "introduction to",
            "fundamentals",
            "principles",
            "theory",
            "methods",
            "research",
            "training",
            "development",
            "management",
            "technical",
            "engineering",
            "science",
            "business",
            "reference",
        }

        subjects = book.get("subjects") or []
        title = (book.get("title") or "").lower()

        # Check subjects for educational indicators
        for subject in subjects:
            subject_lower = subject.lower()
            for indicator in educational_indicators:
                if indicator in subject_lower:
                    return self.weights.get("educational_subject", 0)

        # Also check title for common educational patterns
        educational_title_patterns = [
            "handbook",
            "textbook",
            "introduction to",
            "fundamentals of",
            "principles of",
            "guide to",
            "manual",
        ]
        for pattern in educational_title_patterns:
            if pattern in title:
                return self.weights.get("educational_subject", 0) * 0.5

        return 0.0

    def _score_open_library(self, book: Dict) -> float:
        """Additional scoring for Open Library books."""
        s = 0.0

        # Popularity score
        popularity = book.get("popularity_score", 0) or 0
        if popularity > 0:
            s += min(self.weights.get("popularity", 0), math.log1p(popularity) * 5)

        # Edition count indicates staying power
        editions = book.get("edition_count", 0) or 0
        if editions > 1:
            s += min(self.weights.get("edition_count", 0), editions * 2)

        # Subject match score from fuzzy matching
        subject_match = book.get("subject_match_score", 0) or 0
        s += subject_match * self.weights.get("subject_match", 0)

        return s

    def rank(self, books: List[Tuple[int, Dict]]) -> List[Dict]:
        """
        Rank books using source-aware scoring.

        Args:
            books: List of (index, book) tuples

        Returns:
            Sorted list of book dicts (highest score first),
            each with 'ranking_score' attached.
        """
        scored = [(idx, book, self.score(idx, book)) for idx, book in books]
        scored.sort(key=lambda x: x[2], reverse=True)
        for _, book, s in scored:
            book["ranking_score"] = round(s, 2)
        return [book for _, book, _ in scored]


def rank_books(
    books: List[Tuple[int, Dict]], source: str = "google_books"
) -> List[Dict]:
    """
    Rank books using source-aware scoring.

    Args:
        books: List of (index, book) tuples
        source: Source name for source-specific scoring

    Returns:
        Sorted list of book dicts (highest score first)
    """
    ranker = BookRanker(source)
    return ranker.rank(books)
