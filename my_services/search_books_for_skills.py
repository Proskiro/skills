"""
Service: Get skills from DB and fetch book results for each.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from my_scraper.spiders.book_providers.google_books import (
    GoogleBooksClient,  # ← use your existing module
)
from my_tools.db import get_db_connection


def fetch_skills(limit: int = 50) -> List[Dict]:
    sql = """
        SELECT skill_code, preferred_title, description, books_last_fetched_at
        FROM skills
        WHERE skill_type ILIKE 'knowledge' AND description is not NULL
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
            "skill_code": r[0],
            "title": r[1],
            "description": r[2],
            "books_last_fetched_at": r[3],
        }
        for r in rows
    ]


def search_books_for_skill(skill, client, book_limit):
    """
    Use your GoogleBooksClient to fetch books for each skill.
    """
    query = skill["title"] or skill["description"]
    return client.search(query, book_limit)


def filter_books_google_safe(books: List[Dict], min_year: int = 2020) -> List[Dict]:
    """
    Hard quality filters only.
    These remove junk but keep good unrated books.
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
        if not b.get("description"):
            continue

        # Language sanity check (optional but recommended)
        if b.get("language_code") and b["language_code"] != "en":
            continue

        filtered.append(b)

    return filtered


def rank_books_google_safe(books: List[Tuple[int, Dict]]) -> List[Dict]:
    """
    Ranking that works even when ratings are missing.
    Expects (index, book) tuples to preserve Google ordering.
    """

    def score(idx, b):
        s = 0

        # Google relevance (earlier = better)
        s += max(0, 40 - idx)

        # Recency boost (age-based)
        year = b.get("published_year")
        if year:
            age = datetime.utcnow().year - year
            s += max(0, 20 - age)

        # Optional rating boost
        ratings_count = b.get("ratings_count") or 0
        average_rating = b.get("average_rating") or 0

        if ratings_count >= 10:
            # Weight by both count and quality
            s += min(ratings_count, 100)
            s += int(average_rating * 10)

        # Metadata quality
        if b.get("publisher"):
            s += 10
        if b.get("subjects"):
            s += 5
        if b.get("isbn_10"):
            s += 10

        return s

    ranked = sorted(
        books,
        key=lambda x: score(x[0], x[1]),
        reverse=True,
    )

    # Return only the book dicts
    return [b for _, b in ranked]


def should_refresh_books(last_fetched_at) -> bool:
    if last_fetched_at is None:
        return True
    return last_fetched_at < datetime.utcnow() - timedelta(days=30)


def run_search(skill_limit=50, book_limit=40):
    client = GoogleBooksClient()
    skills = fetch_skills(limit=skill_limit)
    results = []

    conn = get_db_connection()

    for i, skill in enumerate(skills, start=1):
        print("\n" + "=" * 60)
        print(f"[{i}/{len(skills)}] Skill: {skill['title']}")
        print("=" * 60)

        books = search_books_for_skill(skill, client, book_limit)
        print(f"Google returned {len(books)} books")

        filtered_books = filter_books_google_safe(books, min_year=2020)
        print(f"After quality filter: {len(filtered_books)} books")

        ranked_books = rank_books_google_safe(list(enumerate(filtered_books)))
        top_books = ranked_books[:5]

        print(f"Returning top {len(top_books)} books\n")

        if not top_books:
            print("  (no books passed filters)")
        else:
            for b in top_books:
                print(f"  - {b['title']} | {b.get('published_year')}")

        results.append(
            {
                "skill_code": skill["skill_code"],
                "skill": skill["title"],
                "books": top_books,
            }
        )

        # 4. Update refresh timestamp
        # with conn.cursor() as cur:
        #     cur.execute(
        #         """
        #         UPDATE skills
        #         SET books_last_fetched_at = NOW()
        #         WHERE skill_code = %s;
        #         """,
        #         (skill["skill_code"],),
        #     )
        # conn.commit()

    conn.close()
    return results


if __name__ == "__main__":
    results = run_search(skill_limit=10, book_limit=40)
