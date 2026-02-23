import os
import random
import re
import time

import requests
from dotenv import load_dotenv

load_dotenv()


def _get_api_keys():
    """
    Load all available Google Books API keys from environment.
    Supports GOOGLE_BOOKS_API_KEY, GOOGLE_BOOKS_API_KEY_2, etc.
    """
    keys = []
    # Primary key
    if key := os.getenv("GOOGLE_BOOKS_API_KEY"):
        keys.append(key)
    # Additional keys (GOOGLE_BOOKS_API_KEY_2, _3, etc.)
    for i in range(2, 27):
        if key := os.getenv(f"GOOGLE_BOOKS_API_KEY_{i}"):
            keys.append(key)
    return keys


class GoogleBooksClient:
    """Google Books API client using requests with key rotation."""

    SOURCE_NAME = "google_books"
    BASE_URL = "https://www.googleapis.com/books/v1/volumes"

    def __init__(self):
        self._api_keys = _get_api_keys()
        self._current_key_index = 0
        if not self._api_keys:
            raise RuntimeError("No Google Books API keys found in environment")
        print(f"  [API] Loaded {len(self._api_keys)} Google Books API key(s)")

    def _get_api_key(self):
        """Get the current API key."""
        return self._api_keys[self._current_key_index]

    def _rotate_key(self):
        """Rotate to the next API key. Returns True if rotation happened."""
        if len(self._api_keys) > 1:
            old_index = self._current_key_index
            self._current_key_index = (self._current_key_index + 1) % len(self._api_keys)
            print(f"  [API] Rotated from key {old_index + 1} to key {self._current_key_index + 1}")
            return True
        return False

    def _get_with_backoff(self, params, max_attempts=8):
        """
        Make a GET request with retry/backoff for 429 + 5xx.
        Rotates API keys on 429 errors before backing off.
        """
        last_response = None
        keys_tried = 0

        for attempt in range(1, max_attempts + 1):
            # Ensure current key is in params
            params["key"] = self._get_api_key()
            last_response = requests.get(self.BASE_URL, params=params, timeout=20)

            # Success
            if last_response.status_code == 200:
                return last_response

            # Rate limit - try rotating key first
            if last_response.status_code == 429:
                if keys_tried < len(self._api_keys) and self._rotate_key():
                    keys_tried += 1
                    continue  # Retry immediately with new key
                
                # All keys exhausted, fall back to backoff
                retry_after = last_response.headers.get("Retry-After")
                if retry_after:
                    try:
                        sleep_s = float(retry_after)
                    except ValueError:
                        sleep_s = 1.0
                else:
                    base = min(60, 2 ** (attempt - 1))
                    sleep_s = base + random.uniform(0, 0.5 * base)

                print(f"  [API] All keys rate-limited, waiting {sleep_s:.1f}s...")
                time.sleep(max(1.0, sleep_s))
                keys_tried = 0  # Reset for next round
                continue

            # Server errors - backoff without key rotation
            if last_response.status_code in (500, 502, 503, 504):
                base = min(60, 2 ** (attempt - 1))
                sleep_s = base + random.uniform(0, 0.5 * base)
                time.sleep(max(1.0, sleep_s))
                continue

            # Other errors: fail immediately
            last_response.raise_for_status()

        # Exhausted attempts
        last_response.raise_for_status()
        return last_response  # not reached

    def fetch_description_by_isbn(self, isbn: str):
        """Look up a single book by ISBN and return its description.

        Used to enrich Open Library results that lack descriptions.
        Returns None if not found or no description available.
        """
        enrichment = self.fetch_enrichment_by_isbn(isbn)
        if enrichment:
            return enrichment.get("description")
        return None

    def fetch_enrichment_by_isbn(self, isbn: str):
        """Look up a book by ISBN and return enrichment fields.

        Returns a dict with description, free_access, thumbnail, and page_count,
        or None if not found.
        """
        params = {"q": f"isbn:{isbn}", "maxResults": 1}
        try:
            response = self._get_with_backoff(params)
            data = response.json()
            items = data.get("items", [])
            if not items:
                return None

            item = items[0]
            volume_info = item.get("volumeInfo", {})
            access_info = item.get("accessInfo", {})

            # Free access info
            viewability = access_info.get("viewability", "")
            access_status = access_info.get("accessViewStatus", "")
            epub_info = access_info.get("epub", {})
            pdf_info = access_info.get("pdf", {})

            free_access = None
            # Prefer previewLink (Google Books page) over webReaderLink (embedded reader)
            # webReaderLink often errors with "can't open this book" for partial/sample access
            preview_url = volume_info.get("previewLink") or access_info.get("webReaderLink")
            if access_status == "FULL_PUBLIC_DOMAIN" or viewability == "ALL_PAGES":
                free_access = {
                    "type": "free",
                    "read_url": preview_url,
                    "epub_available": epub_info.get("isAvailable", False),
                    "epub_download": epub_info.get("downloadLink"),
                    "pdf_available": pdf_info.get("isAvailable", False),
                    "pdf_download": pdf_info.get("downloadLink"),
                }
            elif viewability == "PARTIAL" or access_status == "SAMPLE":
                free_access = {
                    "type": "preview",
                    "read_url": preview_url,
                    "epub_available": False,
                    "pdf_available": False,
                }

            # Thumbnail
            thumbnail = (
                volume_info.get("imageLinks", {}).get("thumbnail") or ""
            ).replace("http://", "https://") or None

            return {
                "description": volume_info.get("description"),
                "free_access": free_access,
                "thumbnail": thumbnail,
                "page_count": volume_info.get("pageCount"),
            }
        except Exception:
            pass
        return None

    def get_total_results(self, query: str) -> int:
        """Get total number of books matching a query (without fetching results).

        Useful as a popularity signal for skills.
        """
        params = {
            "q": query,
            "maxResults": 1,  # Minimal fetch, we only want totalItems
        }

        response = self._get_with_backoff(params)
        data = response.json()
        return data.get("totalItems", 0)

    def search(self, query, max_results=40):
        """Search for books using Google Books API.

        Returns:
            tuple: (list of book dicts, total_items count from API)
        """
        results = []
        start_index = 0
        total_items = 0

        while len(results) < max_results:
            params = {
                "q": query,
                "maxResults": min(40, max_results - len(results)),
                "startIndex": start_index,
            }

            response = self._get_with_backoff(params)

            data = response.json()
            items = data.get("items", [])

            # Capture total on first request
            if start_index == 0:
                total_items = data.get("totalItems", 0)

            if not items:
                break

            results.extend(self._parse_results(data))
            start_index += len(items)

            # Google hard stop safety
            if start_index >= data.get("totalItems", 0):
                break

        return results, total_items

    def _parse_results(self, data):
        """Convert API JSON into simple Python dicts."""
        items = data.get("items", [])
        results = []

        for item in items:
            volume_info = item.get("volumeInfo", {})
            # Extract ISBNs
            isbn_10 = None
            isbn_13 = None
            for ident in volume_info.get("industryIdentifiers", []):
                if ident["type"] == "ISBN_10":
                    isbn_10 = ident["identifier"]
                elif ident["type"] == "ISBN_13":
                    isbn_13 = ident["identifier"]

            # Extract publication year
            raw_date = volume_info.get("publishedDate")
            year = None

            if raw_date:
                match = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", raw_date)
                if match:
                    year = int(match.group(0))

            # Extract free access information
            access_info = item.get("accessInfo", {})
            viewability = access_info.get("viewability", "")
            access_status = access_info.get("accessViewStatus", "")
            epub_info = access_info.get("epub", {})
            pdf_info = access_info.get("pdf", {})
            
            # Determine free access type
            # Prefer previewLink (Google Books page) over webReaderLink (embedded reader)
            preview_url = volume_info.get("previewLink") or access_info.get("webReaderLink")
            free_access = None
            if access_status == "FULL_PUBLIC_DOMAIN" or viewability == "ALL_PAGES":
                free_access = {
                    "type": "free",
                    "read_url": preview_url,
                    "epub_available": epub_info.get("isAvailable", False),
                    "epub_download": epub_info.get("downloadLink"),
                    "pdf_available": pdf_info.get("isAvailable", False),
                    "pdf_download": pdf_info.get("downloadLink"),
                }
            elif viewability == "PARTIAL" or access_status == "SAMPLE":
                free_access = {
                    "type": "preview",
                    "read_url": preview_url,
                    "epub_available": False,
                    "pdf_available": False,
                }

            results.append(
                {
                    "source": "google_books",
                    "external_id": item.get("id"),
                    "isbn_10": isbn_10,
                    "isbn_13": isbn_13,
                    "title": volume_info.get("title"),
                    "subtitle": volume_info.get("subtitle"),
                    "authors": volume_info.get("authors"),
                    "description": volume_info.get("description"),
                    "subjects": volume_info.get("categories"),
                    "language_code": volume_info.get("language"),
                    "publisher": volume_info.get("publisher"),
                    "published_year": year,
                    "page_count": volume_info.get("pageCount"),
                    "average_rating": volume_info.get("averageRating"),
                    "ratings_count": volume_info.get("ratingsCount"),
                    "thumbnail": (volume_info.get("imageLinks", {}).get("thumbnail") or "").replace("http://", "https://") or None,
                    "semantic_relevance_score": None,  # To be filled later if needed
                    "free_access": free_access,  # Free/preview access info
                    "metadata": item,
                }
            )

        return results
