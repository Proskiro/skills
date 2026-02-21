"""
Populate google_books_total column for skills using pre-filter book counts.

This fetches actual books from Google Books API using the SAME multi-query
strategy as the book search pipeline, and stores the count of unique books
found (deduped by ISBN, before hard filters) as a popularity signal.

Unlike the old approach (which used the unreliable API totalItems), this
counts real unique books that the API returns.

Note: The main search_books_for_skills.py pipeline also updates this column
during normal operation. This script is useful for backfilling skills that
haven't been processed by the main pipeline yet.

Usage:
    python -m my_services.populate_google_book_counts [--limit 100]
"""

import argparse
import time

from dotenv import load_dotenv
from proskiro_tools.db.connection import SessionLocal
from sqlalchemy import text

from my_scraper.spiders.book_providers.google_books import GoogleBooksClient
from my_services.search_books_for_skills import build_search_query

load_dotenv()


def ensure_column_exists(session):
    """Add google_books_total column if it doesn't exist."""
    session.execute(
        text("""
        ALTER TABLE skills
        ADD COLUMN IF NOT EXISTS google_books_total INTEGER;
    """)
    )
    session.commit()
    print("Ensured google_books_total column exists")


def get_skills_without_counts(session, limit: int | None = None, featured_only: bool = False):
    """Get skills that don't have Google Books counts yet."""
    if featured_only:
        query = text("""
            SELECT DISTINCT s.uri, s.preferred_title, s.description
            FROM skills s
            JOIN occupation_skills os ON s.uri = os.skill_uri
            JOIN occupations o ON os.occupation_uri = o.uri
            WHERE s.google_books_total IS NULL
            AND s.skill_type ILIKE 'knowledge'
            AND s.is_leaf = TRUE
            AND o.is_featured = TRUE
            ORDER BY s.uri
            LIMIT :limit
        """)
    else:
        query = text("""
            SELECT uri, preferred_title, description
            FROM skills
            WHERE google_books_total IS NULL
            AND skill_type ILIKE 'knowledge'
            AND is_leaf = TRUE
            ORDER BY uri
            LIMIT :limit
        """)
    result = session.execute(query, {"limit": limit})
    return result.fetchall()


def update_skill_count(session, skill_uri: str, count: int):
    """Update the Google Books total for a skill."""
    session.execute(
        text("UPDATE skills SET google_books_total = :count WHERE uri = :uri"),
        {"count": count, "uri": skill_uri},
    )
    session.commit()


def _fetch_pre_filter_count(client, skill_dict: dict, book_limit: int) -> int:
    """Fetch books using multi-query strategy and return unique count before filters.

    Mirrors the _fetch_multi_query approach from search_books_for_skills.py:
    3 query variants, deduped by ISBN.
    """
    seen = set()
    total = 0

    for variant in ["default", "practical", "handbook"]:
        query = build_search_query(skill_dict, variant=variant)
        try:
            books, _ = client.search(query, book_limit // 3)
        except Exception:
            continue

        for book in books:
            isbn = book.get("isbn_13") or book.get("isbn_10")
            if isbn and isbn not in seen:
                seen.add(isbn)
                total += 1

    return total


def main():
    parser = argparse.ArgumentParser(
        description="Populate Google Books total counts for skills"
    )
    parser.add_argument("--limit", type=int, default=None, help="Max skills to process")
    parser.add_argument(
        "--delay", type=float, default=1.0, help="Delay between API calls (seconds)"
    )
    parser.add_argument(
        "--book-limit", type=int, default=120, help="Max books to fetch per skill (default: 120)"
    )
    parser.add_argument(
        "--featured-only", action="store_true", help="Only process skills linked to featured professions"
    )
    parser.add_argument(
        "--reset", action="store_true", help="Reset existing counts to NULL before processing"
    )
    args = parser.parse_args()

    client = GoogleBooksClient()
    session = SessionLocal()

    try:
        ensure_column_exists(session)

        if args.reset:
            if args.featured_only:
                result = session.execute(text("""
                    UPDATE skills SET google_books_total = NULL
                    WHERE uri IN (
                        SELECT DISTINCT s.uri
                        FROM skills s
                        JOIN occupation_skills os ON s.uri = os.skill_uri
                        JOIN occupations o ON os.occupation_uri = o.uri
                        WHERE o.is_featured = TRUE
                        AND s.skill_type ILIKE 'knowledge'
                        AND s.is_leaf = TRUE
                        AND s.google_books_total IS NOT NULL
                    )
                """))
            else:
                result = session.execute(text("""
                    UPDATE skills SET google_books_total = NULL
                    WHERE skill_type ILIKE 'knowledge'
                    AND is_leaf = TRUE
                    AND google_books_total IS NOT NULL
                """))
            session.commit()
            print(f"Reset {result.rowcount} existing counts to NULL")

        skills = get_skills_without_counts(session, args.limit, featured_only=args.featured_only)
        total = len(skills)

        print(f"Found {total} skills to process")

        for i, (uri, title, description) in enumerate(skills, 1):
            try:
                skill_dict = {"title": title, "description": description or ""}
                count = _fetch_pre_filter_count(client, skill_dict, args.book_limit)
                update_skill_count(session, uri, count)

                print(f"[{i}/{total}] {title}: {count} unique books (pre-filter)")

                time.sleep(args.delay)

            except Exception as e:
                print(f"[{i}/{total}] ERROR {title}: {e}")
                continue

        print(f"\nDone! Processed {total} skills")

        # Show distribution
        result = session.execute(
            text("""
            SELECT
                CASE
                    WHEN google_books_total = 0 THEN '0'
                    WHEN google_books_total < 10 THEN '1-9'
                    WHEN google_books_total < 20 THEN '10-19'
                    WHEN google_books_total < 40 THEN '20-39'
                    WHEN google_books_total < 60 THEN '40-59'
                    WHEN google_books_total < 100 THEN '60-99'
                    ELSE '100+'
                END as bucket,
                COUNT(*) as count
            FROM skills
            WHERE google_books_total IS NOT NULL
            GROUP BY 1
            ORDER BY 1
        """)
        )

        print("\nDistribution of pre-filter book counts:")
        for row in result:
            print(f"  {row.bucket}: {row.count}")

    finally:
        session.close()


if __name__ == "__main__":
    main()
