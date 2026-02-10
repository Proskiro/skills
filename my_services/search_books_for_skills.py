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
from my_services.book_persistence import (
    link_book_to_skill,
    upsert_book,
)
from my_services.book_ranking import rank_books
from my_services.content_filter import is_occupation_excluded, is_skill_excluded
from my_tools.db import get_db_connection

# Semantic model selection - set via CLI argument
# Options:
#   - "cohere" (default): Cohere rerank API - best quality, profession-aware
#   - "cohere_embed": Cohere embed API - legacy, per-book similarity
_config = {"semantic_model": "cohere"}

# Minimum relevance score from Cohere rerank to include a book
# Scores below this are likely irrelevant matches
MIN_RELEVANCE_SCORE = 0.3
MIN_RELEVANCE_SCORE_FALLBACK = 0.16  # Lower threshold for fallback searches


def ensure_connection(conn):
    """Check if connection is alive, reconnect if needed."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return conn
    except Exception:
        print("  [DB] Connection lost, reconnecting...")
        try:
            conn.close()
        except Exception:
            pass
        return get_db_connection()


def update_google_books_total(conn, skill_uri: str, total: int):
    """Update the google_books_total column for a skill (popularity signal).
    
    Now uses actual filtered book count instead of unreliable API totalItems.
    """
    sql = """
        UPDATE skills 
        SET google_books_total = %s 
        WHERE uri = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (total, skill_uri))


def set_semantic_model(model: str):
    """Set the semantic model to use for similarity calculations."""
    _config["semantic_model"] = model


def _make_embed_reranker(compute_similarity_fn):
    """Create a rerank function from an embedding similarity function."""

    def rerank(skill, books, top_n=10):
        scored = [(b, compute_similarity_fn(skill, b)) for b in books]
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


# Common occupation terms that indicate a book is targeted at a specific profession
# Used to filter out books for OTHER occupations (not the target occupation)
OCCUPATION_INDICATORS = {
    # Healthcare
    "nurse", "nurses", "nursing", "physician", "doctor", "medical", "clinical",
    "therapist", "dentist", "pharmacist", "surgeon", "paramedic", "midwife",
    "veterinary", "veterinarian", "optometrist", "radiologist", "anesthesiologist",
    # Education
    "teacher", "teachers", "educator", "professor", "faculty", "classroom",
    "school principal", "librarian", "tutor",
    # Legal/Finance
    "lawyer", "attorney", "paralegal", "accountant", "auditor", "banker",
    "financial advisor", "tax professional",
    # Technical
    "engineer", "developer", "programmer", "architect", "technician",
    "data scientist", "analyst",
    # Business
    "manager", "executive", "ceo", "cfo", "director", "supervisor",
    "administrator", "coordinator", "consultant",
    # Service
    "chef", "waiter", "receptionist", "concierge", "housekeeper",
    "retail", "sales representative", "customer service",
    # Trades
    "electrician", "plumber", "carpenter", "mechanic", "welder",
    "construction worker", "technician",
    # Creative
    "designer", "artist", "writer", "journalist", "photographer",
    # Other
    "pilot", "driver", "officer", "police", "firefighter", "military",
    "social worker", "counselor", "psychologist",
}


def mentions_different_occupation(book: Dict, target_occupation: str) -> bool:
    """
    Check if a book mentions a specific occupation that's different from the target.
    
    Returns True if book should be filtered out (mentions different occupation).
    Returns False if book is generic or matches target occupation.
    """
    if not target_occupation:
        return False  # No target occupation to compare against
    
    title = (book.get("title") or "").lower()
    description = (book.get("description") or "").lower()
    book_text = f"{title} {description}"
    
    # Normalize target occupation for matching
    target_lower = target_occupation.lower()
    target_words = set(target_lower.split())
    
    # Check each occupation indicator
    for indicator in OCCUPATION_INDICATORS:
        if indicator in book_text:
            # Check if this indicator matches the target occupation
            indicator_words = set(indicator.split())
            if indicator_words & target_words:  # Overlap with target
                continue  # This is fine - matches target occupation
            if indicator in target_lower:
                continue  # Indicator is part of target occupation
            if target_lower in indicator:
                continue  # Target is part of indicator
            
            # Found an occupation indicator that doesn't match target
            return True
    
    return False  # Generic book or matches target


# Trusted publishers get a boost in ranking
TRUSTED_PUBLISHERS = {
    "o'reilly",
    "oreilly",
    "wiley",
    "springer",
    "pearson",
    "manning",
    "packt",
    "apress",
    "mit press",
    "mcgraw-hill",
    "mcgraw hill",
    "addison-wesley",
    "addison wesley",
    "pragmatic",
    "no starch",
    "cambridge university press",
    "oxford university press",
    "harvard business",
    "portfolio",
    "penguin business",
    "hbr",
    "kogan page",
}

# Title patterns that indicate non-professional content
TITLE_RED_FLAGS = {
    "coloring book",
    "colouring book",
    "activity book",
    "workbook",
    "journal",
    "planner",
    "notebook",
    "diary",
    "log book",
    "word search",
    "crossword",
    "puzzle",
    "sudoku",
    "kids",
    "children",
    "toddler",
    "baby",
    "memes",
    "jokes",
    "funny",
}

# Minimum thresholds
MIN_DESCRIPTION_LENGTH = 150
MIN_PAGE_COUNT = 80


def build_search_query(skill: Dict, variant: str = "default", use_occupation: bool = True) -> str:
    """
    Build an optimized search query for Google Books.

    Args:
        skill: Skill dict with 'title', 'description', and optionally 'occupation_title'
        variant: Query variant - 'default', 'practical', or 'handbook'
        use_occupation: Whether to include occupation in query (False for fallback)
    """
    title = skill["title"]
    occupation = skill.get("occupation_title", "") if use_occupation else ""

    if variant == "practical":
        if occupation:
            return f"{title} {occupation} practical guide professional"
        return f"{title} practical guide professional"
    elif variant == "handbook":
        if occupation:
            return f"{title} {occupation} handbook"
        return f"{title} handbook"
    else:
        # Default: use title + occupation + key terms from description
        desc_words = skill.get("description", "")[:100]  # First 100 chars
        if occupation:
            return f"{title} {occupation} {desc_words}"
        return f"{title} {desc_words}"


def is_trusted_publisher(publisher: str) -> bool:
    """Check if publisher is in the trusted list."""
    if not publisher:
        return False
    publisher_lower = publisher.lower()
    return any(trusted in publisher_lower for trusted in TRUSTED_PUBLISHERS)


def has_title_red_flags(title: str) -> bool:
    """Check if title contains red flag patterns."""
    if not title:
        return False
    title_lower = title.lower()
    return any(flag in title_lower for flag in TITLE_RED_FLAGS)


def description_quality_check(book: Dict) -> bool:
    """
    Check if description meets quality standards.

    Returns True if description is good enough.
    """
    description = book.get("description", "")
    if not description:
        return False

    # Minimum length check
    if len(description) < MIN_DESCRIPTION_LENGTH:
        return False

    # Check for some keyword overlap between title and description
    title = book.get("title", "").lower()
    desc_lower = description.lower()

    # Extract meaningful words from title (3+ chars)
    title_words = {w for w in title.split() if len(w) >= 3}

    # At least one title word should appear in description
    if title_words and not any(word in desc_lower for word in title_words):
        return False

    return True


def fetch_skills(limit: int = 50, featured_only: bool = False) -> List[Dict]:
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
        {featured_filter}
        ORDER BY skill_code
        LIMIT %s;
    """.format(featured_filter="AND o.is_featured = TRUE" if featured_only else "")

    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(sql, (limit,))
        rows = cur.fetchall()

    conn.close()

    skills = []
    excluded_count = 0
    
    for r in rows:
        skill_uri = r[0]
        skill_title = r[2]
        skill_description = r[3]
        occupation_uri = r[5]
        occupation_title = r[6]
        
        # Apply content filter
        if is_occupation_excluded(occupation_uri or "", occupation_title or ""):
            excluded_count += 1
            continue
        if is_skill_excluded(skill_uri, skill_title, skill_description):
            excluded_count += 1
            continue
        
        skills.append({
            "uri": skill_uri,
            "occupation_uri": occupation_uri,
            "occupation_title": occupation_title,
            "skill_code": r[1],
            "title": skill_title,
            "description": skill_description,
            "books_last_fetched_at": r[4],
        })
    
    if excluded_count > 0:
        print(f"  [CONTENT FILTER] Excluded {excluded_count} skills/occupations")
    
    return skills


def filter_books(
    books: List[Dict],
    min_year: int = 2020,
    require_description: bool = True,
    target_occupation: str = None,
) -> List[Dict]:
    """
    Hard quality filters only (no semantic filtering).
    Semantic relevance is handled separately by rerank.

    Args:
        books: List of book dicts
        min_year: Minimum publication year
        require_description: If False, skip description check
            (Open Library doesn't return descriptions in search)
        target_occupation: If provided, filter out books targeting different occupations
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

        # Title red flags (coloring books, journals, kids books, etc.)
        if has_title_red_flags(b.get("title", "")):
            print(f"    [RED FLAG] {b.get('title', '')[:50]}")
            continue

        # Page count filter (skip pamphlets/booklets)
        page_count = b.get("page_count")
        if page_count and page_count < MIN_PAGE_COUNT:
            print(f"    [TOO SHORT] {b.get('title', '')[:40]} ({page_count} pages)")
            continue

        # Description quality gate
        if require_description and not description_quality_check(b):
            print(f"    [POOR DESC] {b.get('title', '')[:50]}")
            continue

        # Filter out books targeting a different occupation
        if target_occupation and mentions_different_occupation(b, target_occupation):
            print(f"    [WRONG OCCUPATION] {b.get('title', '')[:50]}")
            continue

        filtered.append(b)

    return filtered


def should_refresh_books(last_fetched_at) -> bool:
    if last_fetched_at is None:
        return True
    return last_fetched_at < datetime.utcnow() - timedelta(days=1)


def has_books_from_source(
    conn, skill_uri: str, occupation_uri: str, source: str, max_age_days: int = 1
) -> bool:
    """
    Check if a skill-occupation pair already has books from a specific source within the freshness window.
    """
    sql = """
        SELECT 1
        FROM skill_book_matches sbm
        JOIN books b ON b.id = sbm.book_id
        WHERE sbm.skill_uri = %s
          AND sbm.occupation_uri = %s
          AND b.source = %s
          AND sbm.matched_at >= NOW() - INTERVAL '%s days'
        LIMIT 1;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (skill_uri, occupation_uri, source, max_age_days))
        return cur.fetchone() is not None


def run_search(skill_limit=1000, book_limit=60, min_year=2020, force_refresh=False, max_age_days=1, featured_only=False):
    # TODO: Re-enable OpenLibraryClient() when search quality improves
    clients = [GoogleBooksClient()]
    skills = fetch_skills(limit=skill_limit, featured_only=featured_only)
    results = []

    conn = get_db_connection()

    if featured_only:
        print("Filtering to featured occupations only")
    if force_refresh:
        print("Force refresh enabled - ignoring recently fetched check")
    else:
        print(f"Skipping skills fetched within the last {max_age_days} day(s)")

    for i, skill in enumerate(skills, start=1):
        print("\n" + "=" * 60)
        print(
            f"[{i}/{len(skills)}] Skill: {skill['title']} (for {skill.get('occupation_title', 'general')})"
        )
        print("=" * 60)

        for client in clients:
            source_name = client.SOURCE_NAME  # Each client should define this

            # Skip if this source was recently fetched for this skill-occupation pair (unless force_refresh)
            if not force_refresh and has_books_from_source(
                conn, skill["uri"], skill["occupation_uri"], source_name, max_age_days=max_age_days
            ):
                print(f"  Skipping {source_name} (recently fetched)")
                continue

            # Multi-query strategy: try different query variants and dedupe
            def fetch_and_filter_books(use_occupation: bool = True):
                """Helper to fetch books with or without occupation in query."""
                books_list = []
                seen = set()
                
                for variant in ["default", "practical", "handbook"]:
                    query = build_search_query(skill, variant=variant, use_occupation=use_occupation)
                    print(f"  Query ({variant}): {query[:60]}...")

                    try:
                        books, _ = client.search(query, book_limit // 3)
                    except Exception as e:
                        print(f"  [ERROR] {source_name} ({variant}) failed: {e}")
                        continue

                    for book in books:
                        isbn = book.get("isbn_13") or book.get("isbn_10")
                        if isbn and isbn not in seen:
                            seen.add(isbn)
                            books_list.append(book)
                
                return books_list

            # First attempt: with occupation in query
            all_books = fetch_and_filter_books(use_occupation=True)

            try:
                filtered_books = filter_books(
                    all_books,
                    min_year=min_year,
                    require_description=True,
                    target_occupation=skill.get("occupation_title"),
                )
            except Exception as e:
                print(f"  [ERROR] filtering failed: {e}")
                continue

            print(f"  {source_name}: {len(filtered_books)} books after hard filters")

            # Fallback: if too few results, try without occupation in query
            MIN_BOOKS_THRESHOLD = 3
            used_fallback = False
            if len(filtered_books) < MIN_BOOKS_THRESHOLD and skill.get("occupation_title"):
                print(f"  [FALLBACK] Only {len(filtered_books)} books, trying generic search...")
                used_fallback = True
                
                fallback_books = fetch_and_filter_books(use_occupation=False)
                
                # Filter fallback books (still exclude wrong occupations)
                try:
                    fallback_filtered = filter_books(
                        fallback_books,
                        min_year=min_year,
                        require_description=True,
                        target_occupation=skill.get("occupation_title"),
                    )
                except Exception as e:
                    print(f"  [ERROR] fallback filtering failed: {e}")
                    fallback_filtered = []
                
                # Merge: add fallback books not already in filtered_books
                existing_isbns = {b.get("isbn_13") or b.get("isbn_10") for b in filtered_books}
                for book in fallback_filtered:
                    isbn = book.get("isbn_13") or book.get("isbn_10")
                    if isbn and isbn not in existing_isbns:
                        filtered_books.append(book)
                        existing_isbns.add(isbn)
                
                print(f"  {source_name}: {len(filtered_books)} books after fallback")

            # Semantic reranking - returns [(book, score), ...] sorted by relevance
            rerank_fn = get_rerank_function()
            reranked = rerank_fn(skill, filtered_books, top_n=10)

            for b, score in reranked:
                print(f"    {b.get('title', '')[:40]}: {score:.2f}")

            # Filter out low-relevance books (use lower threshold if fallback was used)
            relevance_threshold = MIN_RELEVANCE_SCORE_FALLBACK if used_fallback else MIN_RELEVANCE_SCORE
            reranked = [(b, score) for b, score in reranked if score >= relevance_threshold]
            
            if len(reranked) < len(filtered_books):
                print(f"  [RELEVANCE] Filtered to {len(reranked)} books (score >= {relevance_threshold})")

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

            # Ensure connection is alive before DB writes
            conn = ensure_connection(conn)

            for rank, book in enumerate(top_books, start=1):
                book_id = upsert_book(conn, book)
                link_book_to_skill(
                    conn,
                    skill_uri=skill["uri"],
                    occupation_uri=skill["occupation_uri"],
                    book_id=book_id,
                    rank=rank,
                )

            # Update book count for popularity signal (actual filtered count)
            books_found = len(filtered_books)
            if books_found > 0:
                update_google_books_total(conn, skill["uri"], books_found)
                print(f"  Books found (filtered): {books_found}")

            conn.commit()

            # Throttle between skills to avoid rate limits
            # With fallback, each skill can make 6 queries (3 + 3), so be conservative
            time.sleep(1.0 + random.random() * 0.5)

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
        default=3000,
        help="Maximum number of skills to process (default: 3000)",
    )
    parser.add_argument(
        "--book-limit",
        type=int,
        default=120,
        help="Maximum books to fetch per source (default: 120)",
    )
    parser.add_argument(
        "--min-year",
        type=int,
        default=2020,
        help="Minimum publication year (default: 2020)",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=1,
        help="Skip skills fetched within this many days (default: 1)",
    )
    parser.add_argument(
        "--featured-only",
        action="store_true",
        help="Only search skills for featured occupations",
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
        max_age_days=args.max_age_days,
        featured_only=args.featured_only,
    )
