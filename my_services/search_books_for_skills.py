"""
Service: Get skills from DB and fetch book results for each.
"""

from typing import Dict, List

from my_scraper.spiders.book_providers.google_books import (
    GoogleBooksClient,  # ← use your existing module
)
from my_tools.db import get_db_connection


def fetch_skills(limit: int = 50) -> List[Dict]:
    sql = """
        SELECT skill_code, preferred_title, description
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

    return [{"skill_code": r[0], "title": r[1], "description": r[2]} for r in rows]


def search_books_for_skill(skill: Dict, client: GoogleBooksClient):
    """
    Use your GoogleBooksClient to fetch books for each skill.
    """
    query = skill["title"] or skill["description"]
    return client.search(query)


def run_search(limit=10):
    client = GoogleBooksClient()
    skills = fetch_skills(limit=limit)
    results = []
    for skill in skills:
        books = search_books_for_skill(skill, client)
        results.append(
            {
                "skill_code": skill["skill_code"],
                "skill": skill["title"],
                "books": books[:3],  # top 3 only
            }
        )

    return results


if __name__ == "__main__":
    results = run_search(limit=5)
    for row in results:
        print("\nSkill:", row["skill"])
        for b in row["books"]:
            print("  -", b["title"])
