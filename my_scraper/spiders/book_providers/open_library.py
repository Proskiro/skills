import random
import re
import time
from difflib import SequenceMatcher
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()


class OpenLibraryClient:
    """Open Library API client using requests."""

    SOURCE_NAME = "open_library"
    BASE_URL = "https://openlibrary.org"

    # Subjects that indicate fiction - exclude these for educational/professional skills
    FICTION_INDICATORS = {
        "fiction",
        "novel",
        "novels",
        "romance",
        "thriller",
        "thrillers",
        "mystery",
        "mysteries",
        "fantasy",
        "science fiction",
        "horror",
        "suspense",
        "drama",
        "short stories",
        "poetry",
        "poems",
        "literary fiction",
        "young adult fiction",
        "children's fiction",
        "graphic novels",
        "comics",
    }

    # Subject patterns to exclude (inappropriate for professional/educational recommendations)
    BLOCKED_SUBJECT_PATTERNS = [
        r"\berotica\b",
        r"\badult\b",  # As in "adult content", not "adult education"
        r"\bpornograph",
        r"\bsexual\s+content\b",
    ]

    # Fields to request from the API
    SEARCH_FIELDS = [
        "key",
        "title",
        "subtitle",
        "author_name",
        "author_key",
        "first_publish_year",
        "subject",
        "language",
        "publisher",
        "ratings_average",
        "ratings_count",
        "want_to_read_count",
        "currently_reading_count",
        "already_read_count",
        "cover_i",
        "edition_count",
        "ebook_access",
        "isbn",  # Aggregated ISBNs across all editions
    ]

    def search(
        self,
        query: str,
        max_results: int = 40,
        language: str = "eng",
        min_year: Optional[int] = None,
        filter_by_subject: bool = True,
        subject_threshold: float = 0.7,
        exclude_fiction: bool = True,
    ) -> List[Dict]:
        """
        Search for books using Open Library API.

        Args:
            query: Search query (skill title or description)
            max_results: Maximum number of results to return
            language: Language code filter (default: English)
            min_year: Minimum publication year filter
            filter_by_subject: If True, filter results by subject match
            subject_threshold: Fuzzy match threshold (0.0-1.0) for subjects
            exclude_fiction: If True, exclude fiction books (default for educational use)

        Returns:
            List of book dicts in standardized format
        """
        # Build enhanced query with filters
        enhanced_query = self._build_query(query, language, min_year)

        # Fetch more results if filtering, since many will be discarded
        fetch_multiplier = 3 if filter_by_subject else 1
        raw_results = self._fetch_results(
            enhanced_query, max_results * fetch_multiplier
        )
        parsed = self._parse_results(raw_results)

        # Exclude fiction if requested (default for educational/professional skills)
        if exclude_fiction:
            parsed = self._exclude_fiction(parsed)

        # Exclude blocked content
        parsed = self._exclude_blocked_content(parsed)

        # Apply subject filtering
        if filter_by_subject:
            parsed = self._filter_by_subject(parsed, query, subject_threshold)

        return parsed[:max_results]

    def _normalize_text(self, text: str) -> str:
        """Normalize text for fuzzy matching."""
        # Lowercase, remove special chars, collapse whitespace
        text = text.lower()
        text = re.sub(r"[^\w\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _is_fiction(self, book: Dict) -> bool:
        """
        Check if a book appears to be fiction based on subjects.
        Returns True if any subject indicates fiction.
        """
        subjects = book.get("subjects") or []

        for subject in subjects:
            subject_lower = subject.lower().strip()
            # Check if subject is a fiction indicator
            if subject_lower in self.FICTION_INDICATORS:
                return True
            # Also check if subject contains fiction indicators as substrings
            for indicator in self.FICTION_INDICATORS:
                if indicator in subject_lower:
                    return True

        return False

    def _exclude_fiction(self, books: List[Dict]) -> List[Dict]:
        """Filter out fiction books."""
        return [b for b in books if not self._is_fiction(b)]

    def _is_blocked_content(self, book: Dict) -> bool:
        """
        Check if a book contains blocked/inappropriate content.
        """
        subjects = book.get("subjects") or []
        title = book.get("title") or ""

        # Check subjects against blocked patterns
        text_to_check = " ".join(subjects) + " " + title
        text_lower = text_to_check.lower()

        for pattern in self.BLOCKED_SUBJECT_PATTERNS:
            if re.search(pattern, text_lower):
                return True

        return False

    def _exclude_blocked_content(self, books: List[Dict]) -> List[Dict]:
        """Filter out books with blocked/inappropriate content."""
        return [b for b in books if not self._is_blocked_content(b)]

    def _fuzzy_match(self, query: str, subject: str, threshold: float) -> bool:
        """
        Check if query fuzzy-matches a subject.
        For multi-word queries, requires the full phrase to match.
        """
        query_norm = self._normalize_text(query)
        subject_norm = self._normalize_text(subject)

        # Exact substring match - the full query must appear in subject
        if query_norm in subject_norm:
            return True

        # For multi-word queries, be stricter
        query_words = query_norm.split()
        is_phrase = len(query_words) > 1

        if is_phrase:
            # For phrases: require sequence similarity on the full phrase
            ratio = SequenceMatcher(None, query_norm, subject_norm).ratio()
            return ratio >= threshold
        else:
            # Single word: allow word-level matching
            subject_words = set(subject_norm.split())

            # Exact word match
            if query_norm in subject_words:
                return True

            # Fuzzy single-word match
            for s_word in subject_words:
                if len(s_word) > 3:
                    word_ratio = SequenceMatcher(None, query_norm, s_word).ratio()
                    if word_ratio >= threshold:
                        return True

        return False

    def _filter_by_subject(
        self, books: List[Dict], query: str, threshold: float
    ) -> List[Dict]:
        """
        Filter books to those with subjects matching the query.

        Subject matching uses fuzzy matching with the given threshold.
        Title matching is stricter - requires exact word match or substring,
        not fuzzy matching (to avoid false positives like "communication" matching
        random fiction titles).
        """
        filtered = []
        query_norm = self._normalize_text(query)
        query_words = set(query_norm.split())

        for book in books:
            subjects = book.get("subjects") or []
            title = book.get("title") or ""
            title_norm = self._normalize_text(title)
            title_words = set(title_norm.split())

            # Check subjects for match (fuzzy allowed)
            subject_match = any(
                self._fuzzy_match(query, subj, threshold) for subj in subjects
            )

            # Title matching is stricter - require exact substring or word match
            # This prevents "The Wife Upstairs" matching "communication"
            title_match = False
            if query_norm in title_norm:
                # Full query appears as substring in title
                title_match = True
            elif query_words & title_words:
                # At least one query word exactly matches a title word
                title_match = True

            if subject_match or title_match:
                # Subject match is higher quality than title-only match
                book["subject_match_score"] = 1.0 if subject_match else 0.5
                filtered.append(book)

        return filtered

    def _build_query(
        self,
        query: str,
        language: Optional[str] = None,
        min_year: Optional[int] = None,
    ) -> str:
        """
        Build an enhanced search query with filters.
        Open Library uses Solr query syntax.
        """
        parts = [query]

        # Add language filter
        if language:
            parts.append(f"language:{language}")

        # Add year filter (first_publish_year:[2020 TO *])
        if min_year:
            parts.append(f"first_publish_year:[{min_year} TO *]")

        return " ".join(parts)

    def _get_with_backoff(
        self, endpoint: str, params: Optional[dict] = None, max_attempts: int = 8
    ):
        """
        Make a GET request with retry/backoff for 429 + 5xx.
        Respects Retry-After header when present.
        """
        last_response = None
        url = f"{self.BASE_URL}{endpoint}"

        for attempt in range(1, max_attempts + 1):
            last_response = requests.get(url, params=params, timeout=20)

            # Success
            if last_response.status_code == 200:
                return last_response

            # Retry on rate limit / temporary server errors
            if last_response.status_code in (429, 500, 502, 503, 504):
                retry_after = last_response.headers.get("Retry-After")

                if retry_after:
                    try:
                        sleep_s = float(retry_after)
                    except ValueError:
                        sleep_s = 1.0
                else:
                    # 1, 2, 4, 8... seconds (capped) + jitter
                    base = min(60, 2 ** (attempt - 1))
                    sleep_s = base + random.uniform(0, 0.5 * base)

                time.sleep(max(1.0, sleep_s))
                continue

            # Other errors: fail immediately
            last_response.raise_for_status()

        # Exhausted attempts - last_response is guaranteed to be set after loop
        assert last_response is not None
        last_response.raise_for_status()
        return last_response  # not reached

    def _fetch_results(self, query: str, max_results: int) -> List[Dict]:
        """Fetch raw results from Open Library API with pagination."""
        results = []
        page = 1
        page_size = min(100, max_results)  # Open Library allows up to 100 per page

        while len(results) < max_results:
            params = {
                "q": query,
                "page": page,
                "limit": min(page_size, max_results - len(results)),
                "fields": ",".join(self.SEARCH_FIELDS),
                "sort": "rating desc",  # Prioritize highly-rated books
            }
            response = self._get_with_backoff("/search.json", params=params)
            data = response.json()

            docs = data.get("docs", [])
            if not docs:
                break  # No more results

            results.extend(docs)
            page += 1

            # Safety: don't exceed total available
            if len(results) >= data.get("numFound", 0):
                break

        return results[:max_results]

    def _extract_isbn(self, doc: Dict) -> tuple[Optional[str], Optional[str]]:
        """Extract ISBN-10 and ISBN-13 from aggregated isbn field."""
        isbn_10 = None
        isbn_13 = None

        # Open Library returns all ISBNs in a flat list
        isbns = doc.get("isbn", [])

        for isbn in isbns:
            if not isbn:
                continue
            # Clean the ISBN (remove hyphens)
            clean_isbn = isbn.replace("-", "")

            if len(clean_isbn) == 10 and not isbn_10:
                isbn_10 = clean_isbn
            elif len(clean_isbn) == 13 and not isbn_13:
                isbn_13 = clean_isbn

            if isbn_10 and isbn_13:
                break

        return isbn_10, isbn_13

    def _parse_results(self, docs: List[Dict]) -> List[Dict]:
        """
        Convert Open Library API JSON into standardized book dicts.
        Matches the format used by GoogleBooksClient.
        """
        results = []

        for doc in docs:
            isbn_10, isbn_13 = self._extract_isbn(doc)

            # Get first language if available
            languages = doc.get("language", [])
            language_code = languages[0] if languages else None

            # Get first publisher if available
            publishers = doc.get("publisher", [])
            publisher = publishers[0] if publishers else None

            # Calculate popularity score for ranking
            want_to_read = doc.get("want_to_read_count", 0) or 0
            currently_reading = doc.get("currently_reading_count", 0) or 0
            already_read = doc.get("already_read_count", 0) or 0
            popularity_score = want_to_read + currently_reading * 2 + already_read * 3

            results.append(
                {
                    "source": "open_library",
                    "external_id": doc.get("key", "").replace("/works/", ""),
                    "isbn_10": isbn_10,
                    "isbn_13": isbn_13,
                    "title": doc.get("title"),
                    "subtitle": doc.get("subtitle"),
                    "authors": doc.get("author_name"),
                    "description": None,  # Not available in search results
                    "subjects": doc.get("subject"),
                    "language_code": language_code,
                    "publisher": publisher,
                    "published_year": doc.get("first_publish_year"),
                    "average_rating": doc.get("ratings_average"),
                    "ratings_count": doc.get("ratings_count"),
                    "cover_id": doc.get("cover_i"),
                    "edition_count": doc.get("edition_count"),
                    "popularity_score": popularity_score,
                    "metadata": doc,
                }
            )

        return results

    def get_cover_url(self, cover_id: int, size: str = "M") -> Optional[str]:
        """
        Get cover image URL for a book.

        Args:
            cover_id: Cover ID from search results
            size: S (small), M (medium), or L (large)

        Returns:
            Cover URL or None if no cover_id
        """
        if not cover_id:
            return None
        return f"https://covers.openlibrary.org/b/id/{cover_id}-{size}.jpg"

    def get_work_description(self, work_id: str) -> Optional[str]:
        """
        Fetch full description for a work (not included in search results).
        Use sparingly - requires additional API call per book.
        """
        try:
            response = self._get_with_backoff(f"/works/{work_id}.json")
            if response is None:
                return None
            data = response.json()

            description = data.get("description")
            if isinstance(description, dict):
                return description.get("value")
            return description
        except Exception:
            return None
