"""
Populate google_books_total column for skills to use as popularity signal.

This queries Google Books API to get total result counts for each skill,
using the SAME filtered query as the book search (title + description context).

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
    print("✓ Ensured google_books_total column exists")


def get_skills_without_counts(session, limit: int | None = None):
    """Get skills that don't have Google Books counts yet."""
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


def main():
    parser = argparse.ArgumentParser(
        description="Populate Google Books total counts for skills"
    )
    parser.add_argument("--limit", type=int, default=None, help="Max skills to process")
    parser.add_argument(
        "--delay", type=float, default=0.5, help="Delay between API calls (seconds)"
    )
    args = parser.parse_args()

    client = GoogleBooksClient()
    session = SessionLocal()

    try:
        ensure_column_exists(session)

        skills = get_skills_without_counts(session, args.limit)
        total = len(skills)

        print(f"Found {total} skills to process")

        for i, (uri, title, description) in enumerate(skills, 1):
            try:
                # Build the same query used for actual book search
                skill_dict = {"title": title, "description": description or ""}
                query = build_search_query(skill_dict, variant="default")

                # Query Google Books with filtered query
                count = client.get_total_results(query)
                update_skill_count(session, uri, count)

                # Show progress
                stars = "★" * min(5, count // 200) if count > 0 else "☆"
                print(f"[{i}/{total}] {title}: {count:,} books {stars}")
                print(f"          Query: {query[:60]}...")

                time.sleep(args.delay)

            except Exception as e:
                print(f"[{i}/{total}] ERROR {title}: {e}")
                continue

        print(f"\n✓ Done! Processed {total} skills")

        # Show distribution
        result = session.execute(
            text("""
            SELECT 
                CASE 
                    WHEN google_books_total = 0 THEN '0'
                    WHEN google_books_total < 10 THEN '1-9'
                    WHEN google_books_total < 100 THEN '10-99'
                    WHEN google_books_total < 1000 THEN '100-999'
                    WHEN google_books_total < 10000 THEN '1K-10K'
                    ELSE '10K+'
                END as bucket,
                COUNT(*) as count
            FROM skills
            WHERE google_books_total IS NOT NULL
            GROUP BY 1
            ORDER BY 1
        """)
        )

        print("\nDistribution of Google Books totals:")
        for row in result:
            print(f"  {row.bucket}: {row.count}")

    finally:
        session.close()


if __name__ == "__main__":
    main()
