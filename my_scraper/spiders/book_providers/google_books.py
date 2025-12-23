import os
import re

import requests
from dotenv import load_dotenv

load_dotenv()


class GoogleBooksClient:
    """Google Books API client using requests."""

    BASE_URL = "https://www.googleapis.com/books/v1/volumes"

    def search(self, query, max_results=40):
        """Search for books using Google Books API."""
        results = []
        start_index = 0

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

            if not items:
                break

            results.extend(self._parse_results(data))
            start_index += len(items)

            # Google hard stop safety
            if start_index >= data.get("totalItems", 0):
                break

        return results

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
                    "average_rating": volume_info.get("averageRating"),
                    "ratings_count": volume_info.get("ratingsCount"),
                    "metadata": item,
                }
            )

        return results
