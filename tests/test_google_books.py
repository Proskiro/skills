import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

from db.db_books import save_books
from my_scraper.spiders.book_providers.google_books import GoogleBooksClient


def run_test():
    # Connect to DB
    conn = psycopg2.connect(
        dbname=os.getenv("POSTGRES_DB"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        host=os.getenv("POSTGRES_HOST"),
        port=5432,
    )

    client = GoogleBooksClient()
    results = client.search("python programming")

    print(f"Fetched {len(results)} books")
    for b in results[:3]:
        print(b["title"], b["subtitle"], b["authors"], b["average_rating"])

    save_books(conn, results)

    print("Saved to database successfully.")


if __name__ == "__main__":
    run_test()
