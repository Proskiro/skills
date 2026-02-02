"""
Service: Get skills from DB and fetch book results for each.

Pipeline:
1. Fetch skills from DB (knowledge skills with descriptions, leaf nodes only)
2. For each skill, search Google Books and Open Library APIs
3. Filter books through quality gates:
   - Publication year >= min_year (default 2020)
   - Must have ISBN (for Amazon linking)
   - Must have title, authors, and description (Google) or title/authors (Open Library)
   - English language only
   - Exclude fiction based on subject indicators
   - Spam title detection (filters SEO-stuffed titles with unrelated topics like cheese, recipes, etc.)
   - Semantic similarity check (skill description vs book title+description, threshold 0.5)
4. Rank filtered books using book_ranking.py scoring
5. Persist top 5 books per source to DB with skill linkage

CLI Arguments:
    --force-refresh     Ignore recently fetched check, re-fetch all sources
    --skill-limit N     Max skills to process (default 1000)
    --book-limit N      Max books per source (default 40)
    --min-year N        Min publication year (default 2020)
    --semantic-model    'cohere' (rerank, default) or 'cohere_embed' (embed, legacy)
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

# Semantic model selection - set via CLI argument
# Options:
#   - "cohere" (default): Cohere rerank API - best quality, profession-aware
#   - "cohere_embed": Cohere embed API - legacy, per-book similarity
_config = {"semantic_model": "cohere"}


def set_semantic_model(model: str):
    """Set the semantic model to use for similarity calculations."""
    _config["semantic_model"] = model


def _make_embed_reranker(compute_similarity_fn):
    """Create a rerank function from an embedding similarity function."""
    def rerank(skill, books, top_n=10):
        scored = [
            (b, compute_similarity_fn(skill, b))
            for b in books
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_n]
    return rerank


def get_rerank_function():
    """Return the appropriate rerank function based on semantic model config."""
    model = _config["semantic_model"]
    
    if model == "cohere":
        # Cohere rerank: batch API, profession-aware, best quality
        from my_services.semantic_filtering_cohere import rerank_books_for_skill
        return rerank_books_for_skill
    
    else:  # cohere_embed
        # Cohere embed: per-book similarity, legacy option
        from my_services.semantic_filtering_cohere import compute_similarity
        return _make_embed_reranker(compute_similarity)


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


# Spam title detection - unrelated topic combinations
SPAM_INDICATORS = {
    # Food/cooking terms that shouldn't appear in professional books
    "cheese",
    "artisan",
    "artisanal",
    "recipe",
    "recipes",
    "cookbook",
    "cooking",
    "baking",
    "wine",
    "beer",
    "cocktail",
    "cuisine",
    "chef",
    "gourmet",
    "foodie",
    "dessert",
    "pastry",
    "sourdough",
    "ferment",
    "pickle",
    "jam",
    "preserve",
    # Hobby/craft terms
    "knitting",
    "crochet",
    "quilting",
    "scrapbook",
    "origami",
    "pottery",
    "gardening",
    "garden",
    "landscaping",
    "houseplant",
    # Pet/animal care
    "dog training",
    "puppy",
    "kitten",
    "aquarium",
    "terrarium",
    # Travel/lifestyle
    "travel guide",
    "vacation",
    "resort",
    "spa",
    "wellness retreat",
    # Fiction/entertainment sneaking in
    "vampire",
    "zombie",
    "werewolf",
    "dragon",
    "wizard",
    "witch",
}


def is_spam_title(title: str) -> bool:
    """
    Detect spam titles that combine unrelated topics.
    These are often SEO-stuffed titles or mislabeled books.
    """
    title_lower = title.lower()
    for indicator in SPAM_INDICATORS:
        if indicator in title_lower:
            return True
    return False


def fetch_skills(limit: int = 50) -> List[Dict]:
    sql = """
        SELECT s.uri, 
		s.skill_code, 
		s.preferred_title AS skill_title, 
		s.description, 
		s.books_last_fetched_at, 
		os.occupation_uri, 
		o.preferred_title AS occupation_title
        FROM skills s
        
        LEFT JOIN occupation_skills os
        ON s.uri = os.skill_uri
        
        LEFT JOIN occupations o
        ON os.occupation_uri = o.uri
 		WHERE s.skill_type ILIKE 'knowledge' 
 		AND s.description is not NULL 
 		AND o.uri is not NULL
 		AND s.is_leaf = TRUE
 		
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
            "occupation_title": r[6],
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
) -> List[Dict]:
    """
    Hard quality filters only (no semantic filtering).
    Semantic relevance is handled separately by rerank.

    Args:
        books: List of book dicts
        min_year: Minimum publication year
        require_description: If False, skip description check
            (Open Library doesn't return descriptions in search)
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
        if is_fiction(b):
            continue

        # Spam title detection - catches SEO-stuffed titles with unrelated topics
        if is_spam_title(b.get("title", "")):
            print(f"    [SPAM] {b.get('title', '')[:50]}")
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
    # TODO: Re-enable OpenLibraryClient() when search quality improves
    clients = [GoogleBooksClient()]
    skills = fetch_skills(limit=skill_limit)
    results = []

    conn = get_db_connection()

    if force_refresh:
        print("Force refresh enabled - ignoring recently fetched check")

    for i, skill in enumerate(skills, start=1):
        print("\n" + "=" * 60)
        print(f"[{i}/{len(skills)}] Skill: {skill['title']} (for {skill.get('occupation_title', 'general')})")
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
            try:
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
                    # Fetch descriptions for better reranking (limit to top candidates)
                    filtered_books = filter_books(
                        books,
                        min_year=min_year,
                        require_description=False,
                    )
                    # Enrich with descriptions before reranking
                    print(f"  Fetching descriptions for {min(len(filtered_books), 20)} Open Library books...")
                    client.enrich_with_descriptions(filtered_books, max_books=20, delay=0.1)
                else:
                    books = client.search(query, book_limit)
                    filtered_books = filter_books(
                        books,
                        min_year=min_year,
                        require_description=True,
                    )
            except Exception as e:
                print(f"  [ERROR] {source_name} failed: {e}")
                continue

            print(f"  {source_name}: {len(filtered_books)} books after hard filters")

            # Semantic reranking - returns [(book, score), ...] sorted by relevance
            rerank_fn = get_rerank_function()
            reranked = rerank_fn(skill, filtered_books, top_n=10)
            
            for b, score in reranked:
                print(f"    {b.get('title', '')[:40]}: {score:.2f}")

            # Extract just the books for further ranking
            semantically_filtered = [b for b, score in reranked]

            ranked_books = rank_books(
                list(enumerate(semantically_filtered)), source=source_name
            )
            top_books = ranked_books[:5]

            print(f"  Returning top {len(top_books)} books")

            if not top_books:
                print("    (no books passed filters)")
            else:
                for b in top_books:
                    print(f"    - {b.get('title', '')} | {b.get('published_year', '')}")

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
    parser.add_argument(
        "--semantic-model",
        type=str,
        choices=["cohere", "cohere_embed"],
        default="cohere",
        help="Semantic model: 'cohere' (rerank, best) or 'cohere_embed' (embed, legacy)",
    )
    args = parser.parse_args()

    # Set the semantic model before running search
    set_semantic_model(args.semantic_model)
    print(f"Using semantic model: {args.semantic_model}")

    results = run_search(
        skill_limit=args.skill_limit,
        book_limit=args.book_limit,
        min_year=args.min_year,
        force_refresh=args.force_refresh,
    )
