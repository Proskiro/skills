import random
import time
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()


class OpenLibraryClient:
    """Open Library API client matching GoogleBooksClient interface.

    Returns (results, total_items) from search(), uses SOURCE_NAME,
    and produces the same dict format so the shared pipeline
    (spam filters, occupation filters, semantic rerank) works unchanged.

    Note: Open Library does not return descriptions in search results.
    Descriptions are enriched in the pipeline via Google Books ISBN lookup,
    then Open Library Works API, then synthetic fallback.
    """

    SOURCE_NAME = "open_library"
    BASE_URL = "https://openlibrary.org"

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
        "number_of_pages_median",
        "ratings_average",
        "ratings_count",
        "want_to_read_count",
        "currently_reading_count",
        "already_read_count",
        "cover_i",
        "edition_count",
        "ebook_access",
        "isbn",
    ]

    def __init__(self):
        print("  [API] Open Library client ready (no key required)")

    def search(self, query: str, max_results: int = 40):
        """Search for books using Open Library API.

        Returns:
            tuple: (list of book dicts, total_items count from API)
                   Same interface as GoogleBooksClient.search()
        """
        raw_results, total_items = self._fetch_results(query, max_results)
        parsed = self._parse_results(raw_results)
        return parsed[:max_results], total_items

    def get_total_results(self, query: str) -> int:
        """Get total number of books matching a query."""
        params = {
            "q": query,
            "limit": 1,
            "fields": "key",
            "language": "eng",
        }
        response = self._get_with_backoff("/search.json", params=params)
        data = response.json()
        return data.get("numFound", 0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_with_backoff(
        self, endpoint: str, params: Optional[dict] = None, max_attempts: int = 8
    ):
        """GET with retry/backoff for 429 + 5xx."""
        last_response = None
        url = f"{self.BASE_URL}{endpoint}"

        for attempt in range(1, max_attempts + 1):
            last_response = requests.get(url, params=params, timeout=20)

            if last_response.status_code == 200:
                return last_response

            if last_response.status_code in (429, 500, 502, 503, 504):
                retry_after = last_response.headers.get("Retry-After")
                if retry_after:
                    try:
                        sleep_s = float(retry_after)
                    except ValueError:
                        sleep_s = 1.0
                else:
                    base = min(60, 2 ** (attempt - 1))
                    sleep_s = base + random.uniform(0, 0.5 * base)

                time.sleep(max(1.0, sleep_s))
                continue

            last_response.raise_for_status()

        assert last_response is not None
        last_response.raise_for_status()
        return last_response  # not reached

    def _fetch_results(self, query: str, max_results: int) -> tuple:
        """Fetch raw results from Open Library API with pagination.

        Returns:
            tuple: (list of raw docs, total_found count)
        """
        results = []
        page = 1
        page_size = min(100, max_results)
        total_found = 0

        while len(results) < max_results:
            params = {
                "q": query,
                "page": page,
                "limit": min(page_size, max_results - len(results)),
                "fields": ",".join(self.SEARCH_FIELDS),
                "language": "eng",
                "sort": "rating desc",
            }

            response = self._get_with_backoff("/search.json", params=params)
            data = response.json()

            docs = data.get("docs", [])

            if page == 1:
                total_found = data.get("numFound", 0)

            if not docs:
                break

            results.extend(docs)
            page += 1

            if len(results) >= data.get("numFound", 0):
                break

        return results[:max_results], total_found

    def _extract_isbn(self, doc: Dict) -> tuple:
        """Extract ISBN-10 and ISBN-13 from aggregated isbn field."""
        isbn_10 = None
        isbn_13 = None

        for isbn in doc.get("isbn", []):
            if not isbn:
                continue
            clean = isbn.replace("-", "")
            if len(clean) == 10 and not isbn_10:
                isbn_10 = clean
            elif len(clean) == 13 and not isbn_13:
                isbn_13 = clean
            if isbn_10 and isbn_13:
                break

        return isbn_10, isbn_13

    def _parse_results(self, docs: List[Dict]) -> List[Dict]:
        """Convert Open Library docs into the same dict format as GoogleBooksClient."""
        results = []

        for doc in docs:
            isbn_10, isbn_13 = self._extract_isbn(doc)

            languages = doc.get("language", [])
            language_code = languages[0] if languages else None

            publishers = doc.get("publisher", [])
            publisher = publishers[0] if publishers else None

            cover_id = doc.get("cover_i")
            thumbnail = (
                f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg"
                if cover_id
                else None
            )

            want_to_read = doc.get("want_to_read_count", 0) or 0
            currently_reading = doc.get("currently_reading_count", 0) or 0
            already_read = doc.get("already_read_count", 0) or 0
            popularity_score = want_to_read + currently_reading * 2 + already_read * 3

            year = None
            raw_year = doc.get("first_publish_year")
            if raw_year:
                try:
                    year = int(raw_year)
                except (ValueError, TypeError):
                    pass

            results.append(
                {
                    # Fields shared with GoogleBooksClient
                    "source": "open_library",
                    "external_id": doc.get("key", "").replace("/works/", ""),
                    "isbn_10": isbn_10,
                    "isbn_13": isbn_13,
                    "title": doc.get("title"),
                    "subtitle": doc.get("subtitle"),
                    "authors": doc.get("author_name"),
                    "description": None,  # Enriched later in pipeline
                    "subjects": doc.get("subject"),
                    "language_code": language_code,
                    "publisher": publisher,
                    "published_year": year,
                    "page_count": doc.get("number_of_pages_median"),
                    "average_rating": doc.get("ratings_average"),
                    "ratings_count": doc.get("ratings_count"),
                    "thumbnail": thumbnail,
                    "semantic_relevance_score": None,
                    "free_access": None,
                    "metadata": doc,
                    # Open Library extras (used by BookRanker)
                    "popularity_score": popularity_score,
                    "edition_count": doc.get("edition_count"),
                }
            )

        return results

    # ------------------------------------------------------------------
    # Utility methods (not used in main pipeline, available for ad-hoc use)
    # ------------------------------------------------------------------

    def get_cover_url(self, cover_id: int, size: str = "M") -> Optional[str]:
        """Get cover image URL for a book."""
        if not cover_id:
            return None
        return f"https://covers.openlibrary.org/b/id/{cover_id}-{size}.jpg"

    def get_work_description(self, work_id: str) -> Optional[str]:
        """Fetch full description for a work (additional API call per book)."""
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

    def enrich_with_descriptions(
        self,
        books: List[Dict],
        max_books: Optional[int] = None,
        delay: float = 0.1,
    ) -> List[Dict]:
        """Fetch descriptions for books that don't have them (utility method)."""
        count = 0
        for book in books:
            if max_books and count >= max_books:
                break
            if book.get("description"):
                continue
            work_id = book.get("external_id")
            if not work_id:
                continue
            description = self.get_work_description(work_id)
            if description:
                book["description"] = description
                count += 1
            if delay > 0:
                time.sleep(delay)
        return books
