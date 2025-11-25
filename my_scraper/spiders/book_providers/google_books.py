import requests


class GoogleBooksClient:
    """Google Books API client using requests."""

    BASE_URL = "https://www.googleapis.com/books/v1/volumes"

    api_key = "AIzaSyDVTOQJMEwb3JqgKSNtvDDMQ67hcRiB5fk"

    def search(self, query, max_results=5):
        """Search for books using Google Books API."""
        params = {"q": query, "maxResults": max_results}

        if self.api_key:
            params["key"] = self.api_key

        response = requests.get(self.BASE_URL, params=params)
        response.raise_for_status()

        data = response.json()
        return self._parse_results(data)

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
                year = raw_date.split("-")[0]

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
                    "published_year": int(year) if year else None,
                    "average_rating": volume_info.get("averageRating"),
                    "metadata": item,
                }
            )

        return results
