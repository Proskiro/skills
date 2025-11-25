import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

from my_scraper.settings import (
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_USER,
)
from my_scraper.spiders.book_providers.google_books import GoogleBooksClient
from my_tools.db_books import save_books


def run_test():
    # Connect to DB
    conn = psycopg2.connect(
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        host=POSTGRES_HOST,
        port=5432,
    )

    client = GoogleBooksClient()
    results = client.search("python programming")

    print(f"Fetched {len(results)} books")
    for b in results[:3]:
        print(b["title"], b["authors"])

    save_books(conn, results)

    print("Saved to database successfully.")


if __name__ == "__main__":
    run_test()
