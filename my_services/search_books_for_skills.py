"""
Service: Get skills from DB and fetch book results for each.
"""

import argparse
import random
import time
from datetime import datetime, timedelta
from typing import Dict, List

from my_scraper.spiders.book_providers.google_books import GoogleBooksClient
from my_scraper.spiders.book_providers.open_library import OpenLibraryClient
from my_services.book_persistence import (
    link_book_to_skill,
    upsert_book,
)
from my_services.book_ranking import rank_books
from my_tools.db import get_db_connection

# Fiction indicators in subjects/categories
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


def is_fiction(book: Dict) -> bool:
    """Check if book appears to be fiction based on subjects."""
    subjects = book.get("subjects") or []
    for subject in subjects:
        subject_lower = subject.lower().strip()
        if subject_lower in FICTION_INDICATORS:
            return True
        for indicator in FICTION_INDICATORS:
            if indicator in subject_lower:
                return True
    return False


def fetch_skills(limit: int = 50) -> List[Dict]:
    sql = """
        SELECT uri, skill_code, preferred_title, description, books_last_fetched_at
        FROM skills
        WHERE skill_type ILIKE 'knowledge' AND description is not NULL AND is_leaf = TRUE
        ORDER BY skill_code
        LIMIT %s;
    """

    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(sql, (limit,))
        rows = cur.fetchall()

    conn.close()

    return [
        {
            "uri": r[0],
            "skill_code": r[1],
            "title": r[2],
            "description": r[3],
            "books_last_fetched_at": r[4],
        }
        for r in rows
    ]


def search_books_for_skill(skill, client, book_limit):
    """
    Use your GoogleBooksClient to fetch books for each skill.
    """
    query = skill["title"] or skill["description"]
    return client.search(query, book_limit)


def filter_books(
    books: List[Dict],
    min_year: int = 2020,
    require_description: bool = True,
    exclude_fiction: bool = True,
) -> List[Dict]:
    """
    Hard quality filters only.
    These remove junk but keep good unrated books.

    Args:
        books: List of book dicts
        min_year: Minimum publication year
        require_description: If False, skip description check
            (Open Library doesn't return descriptions in search)
        exclude_fiction: If True, filter out fiction books
    """
    filtered = []

    for b in books:
        # Must have a publication year and be recent
        year = b.get("published_year")
        if not year or year < min_year:
            continue

        # Must have at least one ISBN (Amazon linking)
        if not (b.get("isbn_10") or b.get("isbn_13")):
            continue

        # Must have basic metadata
        if not b.get("title"):
            continue
        if not b.get("authors"):
            continue
        if require_description and not b.get("description"):
            continue

        # Language sanity check (optional but recommended)
        lang = b.get("language_code")
        if lang and lang not in ("en", "eng"):
            continue

        # Exclude fiction if requested
        if exclude_fiction and is_fiction(b):
            continue

        filtered.append(b)

    return filtered


def should_refresh_books(last_fetched_at) -> bool:
    if last_fetched_at is None:
        return True
    return last_fetched_at < datetime.utcnow() - timedelta(days=30)


def has_books_from_source(
    conn, skill_uri: str, source: str, max_age_days: int = 30
) -> bool:
    """
    Check if a skill already has books from a specific source within the freshness window.
    """
    sql = """
        SELECT 1
        FROM skill_book_matches sbm
        JOIN books b ON b.id = sbm.book_id
        WHERE sbm.skill_uri = %s
          AND b.source = %s
          AND sbm.matched_at >= NOW() - INTERVAL '%s days'
        LIMIT 1;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (skill_uri, source, max_age_days))
        return cur.fetchone() is not None


def run_search(skill_limit=1000, book_limit=40, min_year=2020, force_refresh=False):
    clients = [GoogleBooksClient(), OpenLibraryClient()]
    skills = fetch_skills(limit=skill_limit)
    results = []

    conn = get_db_connection()

    if force_refresh:
        print("Force refresh enabled - ignoring recently fetched check")

    for i, skill in enumerate(skills, start=1):
        print("\n" + "=" * 60)
        print(f"[{i}/{len(skills)}] Skill: {skill['title']}")
        print("=" * 60)

        for client in clients:
            source_name = client.SOURCE_NAME  # Each client should define this

            # Skip if this source was recently fetched for this skill (unless force_refresh)
            if not force_refresh and has_books_from_source(
                conn, skill["uri"], source_name, max_age_days=30
            ):
                print(f"  Skipping {source_name} (recently fetched)")
                continue

            # Build query from skill
            query = skill["title"] or skill["description"]

            # Call search with source-specific params
            if source_name == "open_library":
                # Open Library has built-in filtering and subject matching
                books = client.search(
                    query,
                    max_results=book_limit,
                    language="eng",
                    min_year=min_year,
                    filter_by_subject=True,
                    subject_threshold=0.7,
                )
                # Open Library doesn't return descriptions in search results
                filtered_books = filter_books(
                    books,
                    min_year=min_year,
                    require_description=False,
                    exclude_fiction=True,
                )
            else:
                books = client.search(query, book_limit)
                filtered_books = filter_books(
                    books,
                    min_year=min_year,
                    require_description=True,
                    exclude_fiction=True,
                )

            print(f"  {source_name}: {len(filtered_books)} books after filter")

            ranked_books = rank_books(
                list(enumerate(filtered_books)), source=source_name
            )
            top_books = ranked_books[:5]

            print(f"  Returning top {len(top_books)} books")

            if not top_books:
                print("    (no books passed filters)")
            else:
                for b in top_books:
                    print(f"    - {b['title']} | {b.get('published_year')}")

            results.append(
                {
                    "skill_uri": skill["uri"],
                    "skill": skill["title"],
                    "source": source_name,
                    "books": top_books,
                }
            )

            for rank, book in enumerate(top_books, start=1):
                book_id = upsert_book(conn, book)
                link_book_to_skill(
                    conn,
                    skill_uri=skill["uri"],
                    book_id=book_id,
                    rank=rank,
                )

            conn.commit()

            # Gentle throttle per source
            time.sleep(0.15 + random.random() * 0.20)

    conn.close()
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Search books for skills")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore recently fetched check and re-fetch all sources",
    )
    parser.add_argument(
        "--skill-limit",
        type=int,
        default=1000,
        help="Maximum number of skills to process (default: 1000)",
    )
    parser.add_argument(
        "--book-limit",
        type=int,
        default=40,
        help="Maximum books to fetch per source (default: 40)",
    )
    parser.add_argument(
        "--min-year",
        type=int,
        default=2020,
        help="Minimum publication year (default: 2020)",
    )
    args = parser.parse_args()

    results = run_search(
        skill_limit=args.skill_limit,
        book_limit=args.book_limit,
        min_year=args.min_year,
        force_refresh=args.force_refresh,
    )
