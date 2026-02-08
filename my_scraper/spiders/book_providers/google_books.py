import os
import random
import re
import time

import requests
from dotenv import load_dotenv

load_dotenv()


class GoogleBooksClient:
    """Google Books API client using requests."""

    SOURCE_NAME = "google_books"
    BASE_URL = "https://www.googleapis.com/books/v1/volumes"

    def _get_with_backoff(self, params, max_attempts=8):
        """
        Make a GET request with retry/backoff for 429 + 5xx.
        Respects Retry-After header when present.
        """
        last_response = None

        for attempt in range(1, max_attempts + 1):
            last_response = requests.get(self.BASE_URL, params=params, timeout=20)

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

        # Exhausted attempts
        last_response.raise_for_status()
        return last_response  # not reached

    def get_total_results(self, query: str) -> int:
        """Get total number of books matching a query (without fetching results).

        Useful as a popularity signal for skills.
        """
        params = {
            "q": query,
            "maxResults": 1,  # Minimal fetch, we only want totalItems
            "key": os.getenv("GOOGLE_BOOKS_API_KEY"),
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

            params["key"] = os.getenv("GOOGLE_BOOKS_API_KEY")

            response = requests.get(self.BASE_URL, params=params)
            response.raise_for_status()

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
                    "thumbnail": volume_info.get("imageLinks", {}).get("thumbnail"),
                    "semantic_relevance_score": None,  # To be filled later if needed
                    "metadata": item,
                }
            )

        return results
