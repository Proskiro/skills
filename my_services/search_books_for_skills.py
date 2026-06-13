"""
Service: Get skills from DB and fetch book results for each.

Pipeline:
1. Fetch skills from DB (knowledge skills with descriptions, leaf nodes only)
2. For each skill:
   a. PHASE 1 - Google Books: 3 query variants → hard filters → semantic rerank → top 5
   b. PHASE 2 - Open Library: 3 query variants → dedup against Google's final 5 →
      hard filters (no description required) → description enrichment waterfall
      (Google Books ISBN lookup → OL Works API → synthetic) → semantic rerank → top 5
3. Filter books through quality gates:
   - Publication year >= min_year (default: current_year - 6)
   - Must have ISBN (for Amazon linking)
   - Must have title, authors, and description (Google) or title/authors (Open Library pre-enrichment)
   - English language only
   - Exclude fiction based on subject indicators
   - Spam title detection (filters SEO-stuffed titles with unrelated topics)
   - Semantic similarity check (skill description vs book title+description)
4. Fallback strategies (cascading, if < 3 books found):
   a. Occupation fallback: Remove occupation from query (generic search)
   b. Year fallback: Expand to older books (current_year - 8)
   c. Broader skill fallback: Search parent skill category
   d. Broader + year fallback: Parent skill with books >= 2012
   (Each fallback uses lower relevance threshold: 0.16 vs 0.3)
5. Rank filtered books using book_ranking.py scoring
6. Persist top 5 books per source to DB with skill linkage

CLI Arguments:
    --force-refresh              Bypass the per-pair freshness gate, re-process all pairs
    --skill-limit N              Max skill-occupation pairs to fetch from DB (default 3000)
    --book-limit N               Max books per source (default 120)
    --primary-years-lookback N   Years back for primary search (default 6)
    --fallback-years-lookback N  Years back for year fallback (default 8)
    --freshness_days N           Per-pair shard gate: skip pairs with any
                                 book_search_attempts row newer than N days
                                 (default 90, ignores source column)
    --fill-gaps-only             Only search pairs with zero books
    --featured-only              Only search skills for featured occupations
    --shard CODES                Filter by ISCO group prefix on occupations.isco_code.
                                 Single digit  (--shard=2)     → LEFT(o.isco_code, 2) IN ('C2')
                                 Two digit     (--shard=21)    → LEFT(o.isco_code, 3) IN ('C21')
                                 Comma list    (--shard=21,22) → multiple, same length only
                                 Mixing single and two-digit values is rejected.
    --max-pairs N                Stop cleanly after processing N (skill, occupation) pairs.
                                 Stops between pairs so book_search_attempts stays consistent.
    --semantic-model             'cohere' (rerank, default) or 'cohere_embed' (embed, legacy)

Shard plan for a full manual catalogue pass (≈ half-day sessions).
The 90-day per-pair freshness gate is the default, so --freshness_days is
only needed when you want a different window:

    # Large groups — split by sub-major group
    python -m my_services.search_books_for_skills --shard=21  # Science & engineering professionals
    python -m my_services.search_books_for_skills --shard=22  # Health professionals
    python -m my_services.search_books_for_skills --shard=23  # Teaching professionals
    python -m my_services.search_books_for_skills --shard=24  # Business & admin professionals
    python -m my_services.search_books_for_skills --shard=25  # ICT professionals
    python -m my_services.search_books_for_skills --shard=26  # Legal, social, cultural professionals

    # Technicians — check pair count first; sub-split if needed
    python -m my_services.search_books_for_skills --shard=3

    # Smaller groups — combine as needed
    python -m my_services.search_books_for_skills --shard=1        # Managers
    python -m my_services.search_books_for_skills --shard=7        # Trades
    python -m my_services.search_books_for_skills --shard=4,5      # Clerical + Services
    python -m my_services.search_books_for_skills --shard=6,8,9,0  # Everything else

    # Cap a session at 200 pairs if needed
    python -m my_services.search_books_for_skills --shard=21 --max-pairs=200

    # Override the default 90-day freshness window
    python -m my_services.search_books_for_skills --shard=25 --freshness_days=30


    ISCO-08
│
├── 0  Armed Forces Occupations
│   ├── 01  Commissioned Armed Forces Officers
│   ├── 02  Non-commissioned Armed Forces Officers
│   └── 03  Armed Forces Occupations, Other Ranks
│
├── 1  Managers
│   ├── 11  Chief Executives, Senior Officials and Legislators
│   ├── 12  Administrative and Commercial Managers
│   ├── 13  Production and Specialised Services Managers
│   └── 14  Hospitality, Retail and Other Services Managers
│
├── 2  Professionals
│   ├── 21  Science and Engineering Professionals
│   ├── 22  Health Professionals
│   ├── 23  Teaching Professionals
│   ├── 24  Business and Administration Professionals
│   ├── 25  Information and Communications Technology Professionals
│   └── 26  Legal, Social and Cultural Professionals
│
├── 3  Technicians and Associate Professionals
│   ├── 31  Science and Engineering Associate Professionals
│   ├── 32  Health Associate Professionals
│   ├── 33  Business and Administration Associate Professionals
│   ├── 34  Legal, Social, Cultural and Related Associate Professionals
│   └── 35  Information and Communications Technicians
│
├── 4  Clerical Support Workers
│   ├── 41  General and Keyboard Clerks
│   ├── 42  Customer Services Clerks
│   ├── 43  Numerical and Material Recording Clerks
│   └── 44  Other Clerical Support Workers
│
├── 5  Services and Sales Workers
│   ├── 51  Personal Services Workers
│   ├── 52  Sales Workers
│   ├── 53  Personal Care Workers
│   └── 54  Protective Services Workers
│
├── 6  Skilled Agricultural, Forestry and Fishery Workers
│   ├── 61  Market-oriented Skilled Agricultural Workers
│   ├── 62  Market-oriented Skilled Forestry, Fishery and Hunting Workers
│   └── 63  Subsistence Farmers, Fishers, Hunters and Gatherers
│
├── 7  Craft and Related Trades Workers
│   ├── 71  Building and Related Trades Workers (excluding Electricians)
│   ├── 72  Metal, Machinery and Related Trades Workers
│   ├── 73  Handicraft and Printing Workers
│   ├── 74  Electrical and Electronic Trades Workers
│   └── 75  Food Processing, Woodworking, Garment and Other Craft and Related Trades Workers
│
├── 8  Plant and Machine Operators and Assemblers
│   ├── 81  Stationary Plant and Machine Operators
│   ├── 82  Assemblers
│   └── 83  Drivers and Mobile Plant Operators
│
└── 9  Elementary Occupations
    ├── 91  Cleaners and Helpers
    ├── 92  Agricultural, Forestry and Fishery Labourers
    ├── 93  Labourers in Mining, Construction, Manufacturing and Transport
    ├── 94  Food Preparation Assistants
    ├── 95  Street and Related Sales and Services Workers
    └── 96  Refuse Workers and Other Elementary Workers
"""

import argparse
import random
import re
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

# Skill expansions: maps short/ambiguous skill names to expanded search terms.
# Helps book APIs understand what we're actually looking for.
SKILL_EXPANSIONS = {
    # Tech acronyms
    "mdx": "MDX Multidimensional Expressions OLAP",
    "dax": "DAX Data Analysis Expressions Power BI",
    "sql": "SQL Structured Query Language database",
    "html": "HTML HyperText Markup Language web",
    "css": "CSS Cascading Style Sheets styling",
    "xml": "XML Extensible Markup Language",
    "json": "JSON JavaScript Object Notation",
    "rest": "REST RESTful API web services",
    "soap": "SOAP Simple Object Access Protocol",
    "ajax": "AJAX Asynchronous JavaScript",
    "api": "API Application Programming Interface",
    "orm": "ORM Object-Relational Mapping",
    "mvc": "MVC Model-View-Controller",
    "etl": "ETL Extract Transform Load data",
    "vba": "VBA Visual Basic for Applications Excel",
    "plc": "PLC Programmable Logic Controller",
    "hmi": "HMI Human-Machine Interface",
    "scada": "SCADA Supervisory Control Data Acquisition",
    # Business/analytics acronyms
    "bi": "Business Intelligence analytics",
    "ml": "Machine Learning",
    "ai": "Artificial Intelligence",
    "nlp": "NLP Natural Language Processing",
    "seo": "SEO Search Engine Optimization",
    "crm": "CRM Customer Relationship Management",
    "erp": "ERP Enterprise Resource Planning",
    "ux": "UX User Experience design",
    "ui": "UI User Interface design",
    "qa": "QA Quality Assurance testing",
    "ci": "CI Continuous Integration",
    "cd": "CD Continuous Deployment",
    # Cloud/infra acronyms
    "aws": "AWS Amazon Web Services cloud",
    "gcp": "GCP Google Cloud Platform",
    "iot": "IoT Internet of Things",
    "sap": "SAP enterprise software",
    "bim": "BIM Building Information Modelling",
    "cad": "CAD Computer-Aided Design",
    "cam": "CAM Computer-Aided Manufacturing",
    "gis": "GIS Geographic Information Systems",
    # From the missing skills list
    "moem": "MOEM Micro-Opto-Electro-Mechanical Systems",
    "staf": "STAF Software Testing Automation Framework",
    "btl": "BTL below-the-line marketing technique",
    "lisp": "Lisp programming language",
    "prolog": "Prolog logic programming language",
    "smalltalk": "Smalltalk object-oriented programming language",
    "salt": "Salt SaltStack configuration management DevOps",
    "cam software": "computer-aided manufacturing CAM software",
    "ict communications protocols": "ICT network communications protocols TCP/IP",
    "ict hardware specifications": "ICT computer hardware specifications",
    "hvac": "HVAC heating ventilation air conditioning refrigeration",
}


def expand_skill_title(title: str, alt_label: str = None) -> str:
    """Expand abbreviated/acronym skill titles for better search results.

    Strategy:
    1. Check SKILL_EXPANSIONS dict for known acronyms/terms
    2. Strip parenthetical qualifiers e.g. 'Prolog (computer programming)' -> 'Prolog programming'
    3. Use alt_label if it's meaningfully different and more descriptive
    """
    title_lower = title.lower().strip()

    # 1. Direct expansion lookup
    if title_lower in SKILL_EXPANSIONS:
        return SKILL_EXPANSIONS[title_lower]

    # 2. Strip parentheticals and merge useful words back in
    #    e.g. "Prolog (computer programming)" -> "Prolog computer programming"
    paren_match = re.match(r'^(.+?)\s*\((.+?)\)\s*$', title)
    if paren_match:
        base = paren_match.group(1).strip()
        qualifier = paren_match.group(2).strip()
        # Also check expansion for the base part
        if base.lower() in SKILL_EXPANSIONS:
            return SKILL_EXPANSIONS[base.lower()]
        return f"{base} {qualifier}"

    # 3. If alt_label is meaningfully different, combine with title for richer search
    if alt_label:
        alt_clean = alt_label.strip()
        # Only use alt_label if it's significantly different from title
        if alt_clean.lower() != title_lower and alt_clean.lower() not in title_lower:
            # Extract unique words from alt_label not in the title
            title_words = set(title_lower.split())
            alt_words = [w for w in alt_clean.lower().split() if w not in title_words and len(w) > 2]
            if alt_words:
                return f"{title} {' '.join(alt_words)}"

    return title


def is_short_skill(title: str) -> bool:
    """Check if a skill title is short enough to require exact mention in books."""
    return len(title.strip()) <= 5


def skill_mentioned_in_book(skill_title: str, book: Dict) -> bool:
    """
    For short/acronym skill names, require the skill to appear in the book's
    title or description. Prevents e.g. SQL books matching for MDX skill.
    """
    skill_lower = skill_title.lower().strip()
    title = (book.get("title") or "").lower()
    desc = (book.get("description") or "").lower()
    book_text = f"{title} {desc}"

    # Check for the skill name itself
    if skill_lower in book_text:
        return True

    # Also check expanded form words (e.g. "multidimensional" for MDX)
    expansion = SKILL_EXPANSIONS.get(skill_lower, "")
    if expansion:
        expansion_words = [w.lower() for w in expansion.split() if len(w) > 3]
        # Require at least 2 expansion words to match (avoids false positives)
        matches = sum(1 for w in expansion_words if w in book_text)
        if matches >= 2:
            return True

    return False


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

    Stores the combined pre-filter book count from Google Books + Open Library:
    unique books fetched across all query variants, deduped by ISBN, before
    any hard filters. This is a reliable popularity signal for star ratings.
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
        variant: Query variant - 'default', 'practical', 'handbook', or 'broader'
        use_occupation: Whether to include occupation in query (False for fallback)
    """
    title = expand_skill_title(skill["title"], alt_label=skill.get("alt_label"))
    occupation = skill.get("occupation_title", "") if use_occupation else ""

    if variant == "broader":
        # Use broader skill category to widen the search
        broader = skill.get("broader_skill_title", "")
        if broader:
            return f"{title} {broader} textbook"
        return f"{title} textbook introduction"
    elif variant == "practical":
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


def _parse_shard(shard_arg: str):
    """Parse --shard CLI value into (prefix_length, shard_values).

    Single-digit shards (e.g. "2", "2,3")    -> prefix_length=2, values=['C2', 'C3']
    Two-digit shards   (e.g. "21", "21,22")  -> prefix_length=3, values=['C21', 'C22']

    Mixing single- and two-digit values is rejected. All entries must be digits.
    Returns (None, None) when shard_arg is falsy.
    """
    if shard_arg is None or shard_arg == "":
        return None, None

    parts = [p.strip() for p in shard_arg.split(",") if p.strip()]
    if not parts:
        raise ValueError("--shard cannot be empty")

    for p in parts:
        if not p.isdigit():
            raise ValueError(
                f"--shard values must be digits only, got '{p}' in '{shard_arg}'"
            )

    lengths = {len(p) for p in parts}
    if len(lengths) > 1:
        raise ValueError(
            f"--shard values must all have the same length (no mixing single- "
            f"and two-digit shards). Got: {parts}"
        )

    digit_len = lengths.pop()
    if digit_len not in (1, 2):
        raise ValueError(
            f"--shard values must be 1 or 2 digits each, got length {digit_len}: {parts}"
        )

    prefix_length = 1 + digit_len  # 'C' + N digits
    shard_values = [f"C{p}" for p in parts]
    return prefix_length, shard_values


def _build_skill_query_parts(
    featured_only: bool,
    fill_gaps_only: bool,
    shard_prefix_length,
    shard_values,
    freshness_days,
):
    """Assemble FROM/JOIN/WHERE clauses + parameter list shared by the
    fetch_skills SELECT and the count_pairs COUNT(*) query."""
    joins = [
        "FROM skills s",
        "JOIN occupation_skills os ON s.uri = os.skill_uri",
        "JOIN occupations o ON os.occupation_uri = o.uri",
        "LEFT JOIN skills bs ON s.broader_skill_uri = bs.uri",
    ]
    where_clauses = [
        "s.skill_type ILIKE 'knowledge'",
        "s.description IS NOT NULL",
        "o.uri IS NOT NULL",
        "s.is_leaf = TRUE",
    ]
    params: list = []

    if featured_only:
        where_clauses.append("o.is_featured = TRUE")

    if fill_gaps_only:
        joins.append(
            "LEFT JOIN skill_book_matches sbm "
            "ON sbm.skill_uri = s.uri AND sbm.occupation_uri = os.occupation_uri"
        )
        where_clauses.append("sbm.skill_uri IS NULL")

    if shard_values:
        placeholders = ",".join(["%s"] * len(shard_values))
        where_clauses.append(f"LEFT(o.isco_code, %s) IN ({placeholders})")
        params.append(shard_prefix_length)
        params.extend(shard_values)

    if freshness_days is not None:
        where_clauses.append(
            "NOT EXISTS ("
            "SELECT 1 FROM book_search_attempts bsa "
            "WHERE bsa.skill_uri = s.uri "
            "AND bsa.occupation_uri = os.occupation_uri "
            "AND bsa.searched_at > NOW() - make_interval(days => %s)"
            ")"
        )
        params.append(freshness_days)

    joins_sql = "\n            ".join(joins)
    where_sql = "\n              AND ".join(where_clauses)
    return joins_sql, where_sql, params


def count_pairs(
    featured_only: bool = False,
    fill_gaps_only: bool = False,
    shard_prefix_length=None,
    shard_values=None,
    freshness_days=None,
) -> int:
    """Count (skill, occupation) pairs that match the same filters as fetch_skills.

    Used for pre-run sanity output so we can report shard size and a rough
    runtime estimate before kicking off a long run.
    """
    joins_sql, where_sql, params = _build_skill_query_parts(
        featured_only=featured_only,
        fill_gaps_only=fill_gaps_only,
        shard_prefix_length=shard_prefix_length,
        shard_values=shard_values,
        freshness_days=freshness_days,
    )
    sql = f"""
        SELECT COUNT(*)
            {joins_sql}
            WHERE {where_sql};
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0]
    finally:
        conn.close()


def fetch_skills(
    limit: int = 50,
    featured_only: bool = False,
    fill_gaps_only: bool = False,
    shard_prefix_length=None,
    shard_values=None,
    freshness_days=None,
) -> List[Dict]:
    """Fetch (skill, occupation) pairs ready for book search.

    Args:
        limit: Maximum number of pairs to return.
        featured_only: Restrict to skills attached to featured occupations.
        fill_gaps_only: Restrict to pairs that have zero books in skill_book_matches.
        shard_prefix_length: 2 or 3 — how many characters of o.isco_code to compare.
            When set, shard_values must also be provided.
        shard_values: List of ISCO prefix codes (e.g. ['C2'] or ['C21', 'C22']) used
            as the IN-list for the LEFT(o.isco_code, prefix_length) filter.
        freshness_days: When set, exclude pairs that have any book_search_attempts
            row newer than this many days (any source). force-refresh callers should
            pass None to bypass the gate.

    Note: Per-source 1-day freshness inside the main loop is unrelated to this gate.
    """
    joins_sql, where_sql, params = _build_skill_query_parts(
        featured_only=featured_only,
        fill_gaps_only=fill_gaps_only,
        shard_prefix_length=shard_prefix_length,
        shard_values=shard_values,
        freshness_days=freshness_days,
    )

    sql = f"""
        SELECT s.uri,
               s.skill_code,
               s.preferred_title AS skill_title,
               s.description,
               s.books_last_fetched_at,
               os.occupation_uri,
               o.preferred_title AS occupation_title,
               bs.preferred_title AS broader_skill_title,
               s.alt_label
            {joins_sql}
            WHERE {where_sql}
            ORDER BY s.skill_code
            LIMIT %s;
    """
    params = list(params) + [limit]

    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(sql, params)
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
        broader_skill_title = r[7]
        alt_label = r[8]

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
            "broader_skill_title": broader_skill_title,
            "alt_label": alt_label,
        })

    if excluded_count > 0:
        print(f"  [CONTENT FILTER] Excluded {excluded_count} skills/occupations")

    return skills


def filter_books(
    books: List[Dict],
    min_year: int = 2020,
    require_description: bool = True,
    target_occupation: str = None,
    skill_title: str = None,
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
        skill_title: If provided and short (<= 5 chars), require skill mention in book
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

        # For short/acronym skills, require the skill to be mentioned in the book
        if skill_title and is_short_skill(skill_title) and not skill_mentioned_in_book(skill_title, b):
            print(f"    [NO SKILL MENTION] {b.get('title', '')[:50]}")
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
    Check if a skill-occupation pair was already searched for a specific source
    within the freshness window. Checks both actual book matches AND search
    attempts (so 0-result searches are also considered fresh).
    """
    # Check if there's a recent search attempt (covers 0-result cases)
    attempt_sql = """
        SELECT 1
        FROM book_search_attempts
        WHERE skill_uri = %s
          AND occupation_uri = %s
          AND source = %s
          AND searched_at >= NOW() - INTERVAL '%s days'
        LIMIT 1;
    """
    with conn.cursor() as cur:
        try:
            cur.execute(attempt_sql, (skill_uri, occupation_uri, source, max_age_days))
            if cur.fetchone() is not None:
                return True
        except Exception:
            # Table doesn't exist yet — fall through to legacy check
            conn.rollback()

    # Fallback: check actual book matches (legacy behavior)
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


def _ensure_search_attempts_table(conn):
    """Create the book_search_attempts table if it doesn't exist."""
    sql = """
    CREATE TABLE IF NOT EXISTS book_search_attempts (
        skill_uri TEXT NOT NULL,
        occupation_uri TEXT NOT NULL,
        source TEXT NOT NULL,
        searched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        books_found INT NOT NULL DEFAULT 0,
        PRIMARY KEY (skill_uri, occupation_uri, source)
    );
    """
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def record_search_attempt(conn, skill_uri: str, occupation_uri: str, source: str, books_found: int = 0):
    """Record that a search was attempted for a skill-occupation-source combo."""
    _ensure_search_attempts_table(conn)
    sql = """
    INSERT INTO book_search_attempts (skill_uri, occupation_uri, source, searched_at, books_found)
    VALUES (%s, %s, %s, NOW(), %s)
    ON CONFLICT (skill_uri, occupation_uri, source)
    DO UPDATE SET searched_at = NOW(), books_found = EXCLUDED.books_found;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (skill_uri, occupation_uri, source, books_found))


def _get_existing_isbns_for_skill(
    conn, skill_uri: str, occupation_uri: str, source: str
) -> set:
    """Get ISBNs of books already linked to a skill from a specific source."""
    sql = """
        SELECT b.isbn_10, b.isbn_13
        FROM skill_book_matches sbm
        JOIN books b ON b.id = sbm.book_id
        WHERE sbm.skill_uri = %s
          AND sbm.occupation_uri = %s
          AND b.source = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (skill_uri, occupation_uri, source))
        rows = cur.fetchall()

    isbns = set()
    for isbn_10, isbn_13 in rows:
        if isbn_10:
            isbns.add(isbn_10)
        if isbn_13:
            isbns.add(isbn_13)
    return isbns


def _fetch_multi_query(client, skill, source_name, book_limit, use_occupation=True):
    """Fetch books using 3 query variants, deduped by ISBN within source."""
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


def _collect_isbns(books: List[Dict]) -> set:
    """Collect all ISBN-10 and ISBN-13 values from a list of books."""
    isbns = set()
    for b in books:
        if b.get("isbn_10"):
            isbns.add(b["isbn_10"])
        if b.get("isbn_13"):
            isbns.add(b["isbn_13"])
    return isbns


def _normalize_title(title: str) -> str:
    """Normalize book title for deduplication.

    Removes edition info (e.g., '2nd Edition', 'E-Book', '2020 Edition'), common suffixes,
    and normalizes whitespace to find duplicate books published in different formats/years.
    """
    if not title:
        return ""

    # Convert to lowercase and strip whitespace
    normalized = title.lower().strip()

    # Remove edition markers (e.g., "2nd Edition", "E-Book", "2020 Edition", "International Edition")
    edition_patterns = [
        r',?\s*(1st|2nd|3rd|4th|5th|[0-9]+(st|nd|rd|th))\s+edition\b',
        r',?\s*e[- ]book\b',
        r',?\s*paperback\b',
        r',?\s*hardcover\b',
        r',?\s*international edition\b',
        r',?\s*revised edition\b',
        r',?\s*\(.*edition.*\)',
        r',?\s*\d{4}\s+edition\b',  # Year-based editions (e.g., "2020 Edition")
        r',?\s*:\s*\d{4}\s+edition\b',  # Colon + year (e.g., ": 2020 Edition")
        r'\s*\(\s*e[- ]book\s*\)',
        r'\s*\(\s*\)',  # Empty parentheses
    ]

    for pattern in edition_patterns:
        normalized = re.sub(pattern, '', normalized, flags=re.IGNORECASE)

    # Remove common suffixes/punctuation
    normalized = re.sub(r'[:\-–—,;]+\s*$', '', normalized)

    # Normalize whitespace
    normalized = ' '.join(normalized.split())

    return normalized


def _deduplicate_by_title(books: List[Dict]) -> List[Dict]:
    """Remove duplicate books with same normalized title.

    Keeps the first occurrence (highest ranked) and removes subsequent editions/reprints.
    """
    seen_titles = set()
    deduped = []

    for book in books:
        normalized = _normalize_title(book.get("title", ""))
        if normalized and normalized not in seen_titles:
            seen_titles.add(normalized)
            deduped.append(book)

    return deduped


def _is_isbn_duplicate(book: Dict, exclude_isbns: set) -> bool:
    """Check if a book's ISBNs overlap with the exclude set."""
    book_isbns = {book.get("isbn_10"), book.get("isbn_13")} - {None}
    return bool(book_isbns & exclude_isbns)


def build_synthetic_description(book: Dict) -> str:
    """Build a synthetic description from title/subjects for the reranker.

    Used as a fallback when no real description is available.
    """
    parts = []
    if book.get("title"):
        parts.append(f"A book about {book['title']}.")
    if book.get("subtitle"):
        parts.append(book["subtitle"] + ".")
    subjects = book.get("subjects") or []
    if subjects:
        subject_text = ", ".join(subjects[:5])
        parts.append(f"Covers topics including {subject_text}.")
    if book.get("authors"):
        parts.append(f"By {', '.join(book['authors'][:3])}.")
    return " ".join(parts) if parts else ""


def _process_source(
    client,
    skill: Dict,
    book_limit: int,
    min_year: int,
    require_description: bool,
    rerank_fn,
    exclude_isbns: set = None,
    fallback_min_year: int = None,
) -> tuple[List[Dict], int, int, int]:
    """Process a single book source for a skill.

    Runs the full pipeline: multi-query fetch → dedup → hard filters →
    occupation fallback → year fallback → semantic rerank → rank → return top 5.

    Args:
        client: Book API client (GoogleBooksClient or OpenLibraryClient)
        skill: Skill dict
        book_limit: Max books to fetch per source
        min_year: Minimum publication year
        require_description: Whether to enforce description quality check
        rerank_fn: Semantic reranking function
        exclude_isbns: ISBNs to exclude (cross-source dedup)
        fallback_min_year: Optional older year threshold for year-based fallback

    Returns:
        tuple: (top_books list, fallback_tier int, filtered_count int, pre_filter_count int)
              fallback_tier: 0=primary, 1=occupation, 2=year, 3=broader, 4=broader+year
              pre_filter_count: unique books fetched before any hard filters (popularity signal)
    """
    source_name = client.SOURCE_NAME
    fallback_tier = 0  # 0 = primary search (no fallback)

    # Step 1: Fetch with multi-query strategy
    all_books = _fetch_multi_query(client, skill, source_name, book_limit, use_occupation=True)

    # Step 2: Cross-source dedup (remove books already in another source's final results)
    if exclude_isbns:
        before_dedup = len(all_books)
        all_books = [b for b in all_books if not _is_isbn_duplicate(b, exclude_isbns)]
        deduped_count = before_dedup - len(all_books)
        if deduped_count > 0:
            print(f"  [DEDUP] Removed {deduped_count} books already in Google's top results")

    # Capture pre-filter count as popularity signal (unique books across all query variants)
    pre_filter_count = len(all_books)

    # Step 3: Hard filters
    try:
        filtered_books = filter_books(
            all_books,
            min_year=min_year,
            require_description=require_description,
            target_occupation=skill.get("occupation_title"),
            skill_title=skill.get("title"),
        )
    except Exception as e:
        print(f"  [ERROR] filtering failed: {e}")
        return [], False, 0, pre_filter_count

    print(f"  {source_name}: {len(filtered_books)} books after hard filters")

    # Step 4: Fallback if too few results
    MIN_BOOKS_THRESHOLD = 3
    used_fallback = False
    if len(filtered_books) < MIN_BOOKS_THRESHOLD and skill.get("occupation_title"):
        print(f"  [FALLBACK] Only {len(filtered_books)} books, trying generic search...")
        used_fallback = True
        fallback_tier = 1  # Occupation fallback

        fallback_books = _fetch_multi_query(client, skill, source_name, book_limit, use_occupation=False)

        # Cross-source dedup on fallback too
        if exclude_isbns:
            fallback_books = [b for b in fallback_books if not _is_isbn_duplicate(b, exclude_isbns)]

        try:
            fallback_filtered = filter_books(
                fallback_books,
                min_year=min_year,
                require_description=require_description,
                target_occupation=skill.get("occupation_title"),
                skill_title=skill.get("title"),
            )
        except Exception as e:
            print(f"  [ERROR] fallback filtering failed: {e}")
            fallback_filtered = []

        # Merge: add fallback books not already in filtered_books
        existing_isbns = _collect_isbns(filtered_books)
        for book in fallback_filtered:
            if not _is_isbn_duplicate(book, existing_isbns):
                filtered_books.append(book)
                existing_isbns.update(_collect_isbns([book]))

        print(f"  {source_name}: {len(filtered_books)} books after fallback")

    # Step 4b: Year-based fallback (if STILL too few results)
    used_year_fallback = False
    if (len(filtered_books) < MIN_BOOKS_THRESHOLD and
        fallback_min_year is not None and
        fallback_min_year < min_year):

        print(f"  [FALLBACK-YEAR] Only {len(filtered_books)} books, trying older years (>= {fallback_min_year})...")
        used_year_fallback = True
        fallback_tier = max(fallback_tier, 2)  # Year fallback (or keep higher tier)

        # Re-filter original fetched books with older year threshold
        try:
            older_books = filter_books(
                all_books,
                min_year=fallback_min_year,
                require_description=require_description,
                target_occupation=skill.get("occupation_title"),
                skill_title=skill.get("title"),
            )
        except Exception as e:
            print(f"  [ERROR] year fallback filtering failed: {e}")
            older_books = []

        # Merge with existing (dedup by ISBN)
        existing_isbns = _collect_isbns(filtered_books)
        for book in older_books:
            if not _is_isbn_duplicate(book, existing_isbns):
                filtered_books.append(book)
                existing_isbns.update(_collect_isbns([book]))

        print(f"  {source_name}: {len(filtered_books)} books after year fallback")

    # Step 4c: Broader skill fallback (if STILL too few results)
    used_broader_fallback = False
    if len(filtered_books) < MIN_BOOKS_THRESHOLD and skill.get("broader_skill_title"):
        broader_title = skill["broader_skill_title"]
        print(f"  [FALLBACK-BROADER] Only {len(filtered_books)} books, trying broader skill: '{broader_title}'...")
        used_broader_fallback = True
        fallback_tier = max(fallback_tier, 3)  # Broader skill fallback

        broader_books = []
        seen = set()
        for variant in ["broader"]:
            query = build_search_query(skill, variant=variant, use_occupation=False)
            print(f"  Query (broader): {query[:60]}...")
            try:
                books, _ = client.search(query, book_limit // 3)
            except Exception as e:
                print(f"  [ERROR] {source_name} (broader) failed: {e}")
                continue
            for book in books:
                isbn = book.get("isbn_13") or book.get("isbn_10")
                if isbn and isbn not in seen:
                    seen.add(isbn)
                    broader_books.append(book)

        # Cross-source dedup
        if exclude_isbns:
            broader_books = [b for b in broader_books if not _is_isbn_duplicate(b, exclude_isbns)]

        try:
            broader_filtered = filter_books(
                broader_books,
                min_year=min_year,
                require_description=require_description,
                target_occupation=skill.get("occupation_title"),
                skill_title=None,  # Don't require skill mention — broader search
            )
        except Exception as e:
            print(f"  [ERROR] broader fallback filtering failed: {e}")
            broader_filtered = []

        existing_isbns = _collect_isbns(filtered_books)
        for book in broader_filtered:
            if not _is_isbn_duplicate(book, existing_isbns):
                filtered_books.append(book)
                existing_isbns.update(_collect_isbns([book]))

        print(f"  {source_name}: {len(filtered_books)} books after broader fallback")

        # Final year fallback within broader: go back to 2012 if still too few
        if len(filtered_books) < MIN_BOOKS_THRESHOLD:
            print(f"  [FALLBACK-BROADER-YEAR] Only {len(filtered_books)} books, trying broader with >= 2012...")
            fallback_tier = 4  # Broader + year fallback
            try:
                broader_older = filter_books(
                    broader_books,
                    min_year=2012,
                    require_description=require_description,
                    target_occupation=skill.get("occupation_title"),
                    skill_title=None,
                )
            except Exception as e:
                print(f"  [ERROR] broader year fallback filtering failed: {e}")
                broader_older = []

            existing_isbns = _collect_isbns(filtered_books)
            for book in broader_older:
                if not _is_isbn_duplicate(book, existing_isbns):
                    filtered_books.append(book)
                    existing_isbns.update(_collect_isbns([book]))

            print(f"  {source_name}: {len(filtered_books)} books after broader year fallback (>= 2012)")

    # Step 5: Semantic reranking
    reranked = rerank_fn(skill, filtered_books, top_n=10)

    for b, score in reranked:
        print(f"    {b.get('title', '')[:40]}: {score:.2f}")

    # Use lower threshold if ANY fallback was used
    used_fallback = used_fallback or used_year_fallback or used_broader_fallback
    relevance_threshold = MIN_RELEVANCE_SCORE_FALLBACK if used_fallback else MIN_RELEVANCE_SCORE
    reranked = [(b, score) for b, score in reranked if score >= relevance_threshold]

    if len(reranked) < len(filtered_books):
        print(f"  [RELEVANCE] Filtered to {len(reranked)} books (score >= {relevance_threshold})")

    # Step 6: Rank
    semantically_filtered = [b for b, score in reranked]
    ranked_books = rank_books(list(enumerate(semantically_filtered)), source=source_name)

    # Step 7: Deduplicate by title (remove different editions of same book)
    deduped_books = _deduplicate_by_title(ranked_books)
    if len(deduped_books) < len(ranked_books):
        print(f"  [DEDUP-TITLE] Removed {len(ranked_books) - len(deduped_books)} duplicate editions")

    top_books = deduped_books[:5]

    print(f"  Returning top {len(top_books)} books (fallback_tier={fallback_tier})")

    if not top_books:
        print("    (no books passed filters)")
    else:
        for b in top_books:
            print(f"    - {b.get('title', '')} | {b.get('published_year', '')}")

    return top_books, fallback_tier, len(filtered_books), pre_filter_count


def _persist_books(conn, skill: Dict, top_books: List[Dict], fallback_tier: int = 0):
    """Persist top books to DB and link to skill.

    Args:
        fallback_tier: Search strategy tier (0=primary, 1=occupation, 2=year, 3=broader, 4=broader+year)
    """
    conn = ensure_connection(conn)

    for rank, book in enumerate(top_books, start=1):
        book_id = upsert_book(conn, book)
        link_book_to_skill(
            conn,
            skill_uri=skill["uri"],
            occupation_uri=skill["occupation_uri"],
            book_id=book_id,
            rank=rank,
            fallback_tier=fallback_tier,
            score=book.get("ranking_score", 0.0),
        )

    return conn


def _format_runtime_estimate(pair_count: int, seconds_per_pair: int = 30) -> str:
    """Rough wall-clock estimate, formatted as e.g. '2h 15m' or '45m'."""
    total_seconds = pair_count * seconds_per_pair
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def run_search(
    skill_limit=1000,
    book_limit=60,
    min_year=2020,
    fallback_min_year=None,
    force_refresh=False,
    max_age_days=1,
    featured_only=False,
    fill_gaps_only=False,
    dry_run=False,
    shard_prefix_length=None,
    shard_values=None,
    freshness_days=None,
    max_pairs=None,
):
    google_client = GoogleBooksClient()
    open_library_client = OpenLibraryClient()

    # ----- Pre-run sanity output --------------------------------------------
    if shard_values:
        print(f"Shard active: {shard_values} "
              f"(filter: LEFT(o.isco_code, {shard_prefix_length}) IN {tuple(shard_values)})")
    else:
        print("Shard: <none> (full catalogue)")

    effective_freshness = None if force_refresh else freshness_days
    if force_refresh:
        print("Force refresh enabled — bypassing per-pair freshness gate")
    elif effective_freshness is not None:
        print(f"Per-pair freshness gate: skipping pairs with any "
              f"book_search_attempts row newer than {effective_freshness} days")

    pair_count = count_pairs(
        featured_only=featured_only,
        fill_gaps_only=fill_gaps_only,
        shard_prefix_length=shard_prefix_length,
        shard_values=shard_values,
        freshness_days=effective_freshness,
    )
    print(f"Matching (skill, occupation) pairs: {pair_count:,}")
    print(f"Rough runtime estimate (≈ 30s/pair): {_format_runtime_estimate(pair_count)}")
    if max_pairs is not None:
        print(f"--max-pairs cap: {max_pairs} "
              f"(≈ {_format_runtime_estimate(min(pair_count, max_pairs))} of work)")
    print(f"--skill-limit (DB fetch cap): {skill_limit}")
    print("-" * 60)

    skills = fetch_skills(
        limit=skill_limit,
        featured_only=featured_only,
        fill_gaps_only=fill_gaps_only,
        shard_prefix_length=shard_prefix_length,
        shard_values=shard_values,
        freshness_days=effective_freshness,
    )
    results = []
    conn = get_db_connection()

    if featured_only:
        print("Filtering to featured occupations only")
    print(f"Per-source freshness window inside main loop: {max_age_days} day(s)")

    rerank_fn = get_rerank_function()
    rerank_failures = 0
    MAX_RERANK_FAILURES = 3

    for i, skill in enumerate(skills, start=1):
        # --max-pairs safety valve: stop cleanly *between* pairs so that any
        # book_search_attempts writes for the previous pair are already committed.
        if max_pairs is not None and i > max_pairs:
            print(f"\nReached --max-pairs={max_pairs} cap. Stopping cleanly "
                  f"after {max_pairs} pairs.")
            break

        print("\n" + "=" * 60)
        print(
            f"[{i}/{len(skills)}] Skill: {skill['title']} (for {skill.get('occupation_title', 'general')})"
        )
        print("=" * 60)

        try:
            # ==============================================================
            # PHASE 1: Google Books (runs first, always)
            # ==============================================================
            google_top_isbns = set()

            google_skipped = not force_refresh and has_books_from_source(
                conn, skill["uri"], skill["occupation_uri"], "google_books", max_age_days=max_age_days
            )

            google_pre_filter_count = 0
            if google_skipped:
                print(f"  Skipping google_books (recently fetched)")
                # Still need ISBNs of Google's persisted books for cross-source dedup
                google_top_isbns = _get_existing_isbns_for_skill(
                    conn, skill["uri"], skill["occupation_uri"], "google_books"
                )
            else:
                print("  --- Google Books ---")
                google_top, google_fallback_tier, google_filtered_count, google_pre_filter_count = _process_source(
                    client=google_client,
                    skill=skill,
                    book_limit=book_limit,
                    min_year=min_year,
                    require_description=True,
                    rerank_fn=rerank_fn,
                    fallback_min_year=fallback_min_year,
                )

                if not dry_run:
                    conn = _persist_books(conn, skill, google_top, fallback_tier=google_fallback_tier)
                    record_search_attempt(conn, skill["uri"], skill["occupation_uri"], "google_books", books_found=len(google_top))
                    conn.commit()

                if google_filtered_count > 0:
                    print(f"  Books found (filtered): {google_filtered_count}")

                results.append({
                    "skill_uri": skill["uri"],
                    "skill": skill["title"],
                    "source": "google_books",
                    "books": google_top,
                })

                # Collect ISBNs from Google's FINAL top books only for dedup
                google_top_isbns = _collect_isbns(google_top)

            # ==============================================================
            # PHASE 2: Open Library (deduped against Google's final top 5)
            # ==============================================================
            ol_pre_filter_count = 0
            ol_skipped = not force_refresh and has_books_from_source(
                conn, skill["uri"], skill["occupation_uri"], "open_library", max_age_days=max_age_days
            )

            if ol_skipped:
                print(f"  Skipping open_library (recently fetched)")
            else:
                print("  --- Open Library ---")
                ol_fallback_tier = 0  # Track fallback tier for Open Library

                # Fetch + dedup + hard filters (no description required yet)
                ol_all_books = _fetch_multi_query(
                    open_library_client, skill, "open_library", book_limit, use_occupation=True
                )

                # Capture pre-filter count as popularity signal
                ol_pre_filter_count = len(ol_all_books)

                # Cross-source dedup against Google's final top books
                before_dedup = len(ol_all_books)
                ol_all_books = [b for b in ol_all_books if not _is_isbn_duplicate(b, google_top_isbns)]
                deduped_count = before_dedup - len(ol_all_books)
                if deduped_count > 0:
                    print(f"  [DEDUP] Removed {deduped_count} books already in Google's top results")

                # Hard filters WITHOUT description requirement
                try:
                    ol_filtered = filter_books(
                        ol_all_books,
                        min_year=min_year,
                        require_description=False,
                        target_occupation=skill.get("occupation_title"),
                        skill_title=skill.get("title"),
                    )
                except Exception as e:
                    print(f"  [ERROR] filtering failed: {e}")
                    ol_filtered = []

                print(f"  open_library: {len(ol_filtered)} books after hard filters")

                # Fallback if too few
                MIN_BOOKS_THRESHOLD = 3
                used_fallback = False
                if len(ol_filtered) < MIN_BOOKS_THRESHOLD and skill.get("occupation_title"):
                    print(f"  [FALLBACK] Only {len(ol_filtered)} books, trying generic search...")
                    used_fallback = True
                    ol_fallback_tier = 1  # Occupation fallback

                    fallback_books = _fetch_multi_query(
                        open_library_client, skill, "open_library", book_limit, use_occupation=False
                    )
                    fallback_books = [b for b in fallback_books if not _is_isbn_duplicate(b, google_top_isbns)]

                    try:
                        fallback_filtered = filter_books(
                            fallback_books,
                            min_year=min_year,
                            require_description=False,
                            target_occupation=skill.get("occupation_title"),
                            skill_title=skill.get("title"),
                        )
                    except Exception as e:
                        print(f"  [ERROR] fallback filtering failed: {e}")
                        fallback_filtered = []

                    existing_isbns = _collect_isbns(ol_filtered)
                    for book in fallback_filtered:
                        if not _is_isbn_duplicate(book, existing_isbns):
                            ol_filtered.append(book)
                            existing_isbns.update(_collect_isbns([book]))

                    print(f"  open_library: {len(ol_filtered)} books after fallback")

                # Year-based fallback for Open Library (if STILL too few)
                if (len(ol_filtered) < MIN_BOOKS_THRESHOLD and
                    fallback_min_year is not None and
                    fallback_min_year < min_year):

                    print(f"  [FALLBACK-YEAR] Only {len(ol_filtered)} books, trying older years (>= {fallback_min_year})...")
                    ol_fallback_tier = max(ol_fallback_tier, 2)  # Year fallback

                    try:
                        older_books = filter_books(
                            ol_all_books,
                            min_year=fallback_min_year,
                            require_description=False,
                            target_occupation=skill.get("occupation_title"),
                            skill_title=skill.get("title"),
                        )
                    except Exception as e:
                        print(f"  [ERROR] year fallback filtering failed: {e}")
                        older_books = []

                    # Dedup against Google's top books
                    older_books = [b for b in older_books if not _is_isbn_duplicate(b, google_top_isbns)]

                    # Merge
                    existing_isbns = _collect_isbns(ol_filtered)
                    for book in older_books:
                        if not _is_isbn_duplicate(book, existing_isbns):
                            ol_filtered.append(book)
                            existing_isbns.update(_collect_isbns([book]))

                    print(f"  open_library: {len(ol_filtered)} books after year fallback")
                    used_fallback = True  # Mark that fallback was used for relevance threshold

                # Broader skill fallback for Open Library (if STILL too few)
                if len(ol_filtered) < MIN_BOOKS_THRESHOLD and skill.get("broader_skill_title"):
                    broader_title = skill["broader_skill_title"]
                    print(f"  [FALLBACK-BROADER] Only {len(ol_filtered)} books, trying broader skill: '{broader_title}'...")
                    used_fallback = True
                    ol_fallback_tier = max(ol_fallback_tier, 3)  # Broader skill fallback

                    broader_books = []
                    seen_broader = set()
                    query = build_search_query(skill, variant="broader", use_occupation=False)
                    print(f"  Query (broader): {query[:60]}...")
                    try:
                        books, _ = open_library_client.search(query, book_limit // 3)
                        for book in books:
                            isbn = book.get("isbn_13") or book.get("isbn_10")
                            if isbn and isbn not in seen_broader:
                                seen_broader.add(isbn)
                                broader_books.append(book)
                    except Exception as e:
                        print(f"  [ERROR] open_library (broader) failed: {e}")

                    # Dedup against Google's top books
                    broader_books = [b for b in broader_books if not _is_isbn_duplicate(b, google_top_isbns)]

                    try:
                        broader_filtered = filter_books(
                            broader_books,
                            min_year=min_year,
                            require_description=False,
                            target_occupation=skill.get("occupation_title"),
                            skill_title=None,  # Don't require skill mention — broader search
                        )
                    except Exception as e:
                        print(f"  [ERROR] broader fallback filtering failed: {e}")
                        broader_filtered = []

                    existing_isbns = _collect_isbns(ol_filtered)
                    for book in broader_filtered:
                        if not _is_isbn_duplicate(book, existing_isbns):
                            ol_filtered.append(book)
                            existing_isbns.update(_collect_isbns([book]))

                    print(f"  open_library: {len(ol_filtered)} books after broader fallback")

                    # Final year fallback within broader: go back to 2012 if still too few
                    if len(ol_filtered) < MIN_BOOKS_THRESHOLD:
                        print(f"  [FALLBACK-BROADER-YEAR] Only {len(ol_filtered)} books, trying broader with >= 2012...")
                        ol_fallback_tier = 4  # Broader + year fallback
                        try:
                            broader_older = filter_books(
                                broader_books,
                                min_year=2012,
                                require_description=False,
                                target_occupation=skill.get("occupation_title"),
                                skill_title=None,
                            )
                        except Exception as e:
                            print(f"  [ERROR] broader year fallback filtering failed: {e}")
                            broader_older = []

                        existing_isbns = _collect_isbns(ol_filtered)
                        for book in broader_older:
                            if not _is_isbn_duplicate(book, existing_isbns):
                                ol_filtered.append(book)
                                existing_isbns.update(_collect_isbns([book]))

                        print(f"  open_library: {len(ol_filtered)} books after broader year fallback (>= 2012)")

                # Enrichment waterfall — fetch missing fields from Google Books
                if ol_filtered:
                    # Layer 1: Google Books ISBN lookup (description, free access, thumbnail)
                    needs_enrichment = [
                        b for b in ol_filtered
                        if not b.get("description") or not b.get("free_access") or not b.get("thumbnail")
                    ]
                    if needs_enrichment:
                        print(f"  [ENRICH] Google Books ISBN lookup for {len(needs_enrichment)} books...")
                        for book in needs_enrichment:
                            isbn = book.get("isbn_13") or book.get("isbn_10")
                            if isbn:
                                enrichment = google_client.fetch_enrichment_by_isbn(isbn)
                                if enrichment:
                                    if not book.get("description") and enrichment.get("description"):
                                        book["description"] = enrichment["description"]
                                    if not book.get("free_access") and enrichment.get("free_access"):
                                        book["free_access"] = enrichment["free_access"]
                                    if not book.get("thumbnail") and enrichment.get("thumbnail"):
                                        book["thumbnail"] = enrichment["thumbnail"]
                                    if not book.get("page_count") and enrichment.get("page_count"):
                                        book["page_count"] = enrichment["page_count"]
                                    print(f"    [Google] Enriched: {book.get('title', '')[:50]}")

                    # Layer 2: Open Library Works API (free, already have work IDs)
                    missing = [b for b in ol_filtered if not b.get("description")]
                    if missing:
                        print(f"  [ENRICH] Open Library Works API for {len(missing)} books...")
                        open_library_client.enrich_with_descriptions(missing)

                    # Layer 3: Synthetic description from title/subjects (fallback)
                    for book in ol_filtered:
                        if not book.get("description"):
                            book["description"] = build_synthetic_description(book)

                    enriched_real = sum(1 for b in ol_filtered if not b["description"].startswith("A book about"))
                    print(f"  [ENRICH] {enriched_real}/{len(ol_filtered)} books got real descriptions")

                # Semantic reranking (all books now have some description text)
                reranked = rerank_fn(skill, ol_filtered, top_n=10)

                for b, score in reranked:
                    print(f"    {b.get('title', '')[:40]}: {score:.2f}")

                relevance_threshold = MIN_RELEVANCE_SCORE_FALLBACK if used_fallback else MIN_RELEVANCE_SCORE
                reranked = [(b, score) for b, score in reranked if score >= relevance_threshold]

                if len(reranked) < len(ol_filtered):
                    print(f"  [RELEVANCE] Filtered to {len(reranked)} books (score >= {relevance_threshold})")

                semantically_filtered = [b for b, score in reranked]
                ranked_books = rank_books(list(enumerate(semantically_filtered)), source="open_library")
                ol_top = ranked_books[:5]

                print(f"  Returning top {len(ol_top)} books (fallback_tier={ol_fallback_tier})")

                if not ol_top:
                    print("    (no books passed filters)")
                else:
                    for b in ol_top:
                        print(f"    - {b.get('title', '')} | {b.get('published_year', '')}")

                if not dry_run:
                    conn = _persist_books(conn, skill, ol_top, fallback_tier=ol_fallback_tier)
                    record_search_attempt(conn, skill["uri"], skill["occupation_uri"], "open_library", books_found=len(ol_top))
                    conn.commit()

                results.append({
                    "skill_uri": skill["uri"],
                    "skill": skill["title"],
                    "source": "open_library",
                    "books": ol_top,
                })

            # Update pre-filter book count as popularity signal
            # Combined unique books from both sources before hard filters
            if not dry_run and not (google_skipped and ol_skipped):
                total_pre_filter = google_pre_filter_count + ol_pre_filter_count
                if total_pre_filter > 0:
                    update_google_books_total(conn, skill["uri"], total_pre_filter)
                    conn.commit()
                    print(f"  Pre-filter book count (popularity signal): {total_pre_filter}")

        except Exception as e:
            rerank_failures += 1
            print(f"  [RERANK FATAL] Skill '{skill['title']}' failed: {e}")
            print(f"  [RERANK FATAL] Failure {rerank_failures}/{MAX_RERANK_FAILURES} — skill will be retried on next run")
            # Rollback any partial work for this skill
            try:
                conn.rollback()
            except Exception:
                pass
            if rerank_failures >= MAX_RERANK_FAILURES:
                print(f"\n{'=' * 60}")
                print(f"TERMINATING: {MAX_RERANK_FAILURES} rerank failures reached. Cohere API may be down.")
                print(f"{'=' * 60}")
                break

        # Throttle between skills
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
        default=None,
        help="Minimum publication year (default: current_year - 6)",
    )
    parser.add_argument(
        "--primary-years-lookback",
        type=int,
        default=6,
        help="Years to look back for primary search (default: 6)",
    )
    parser.add_argument(
        "--fallback-years-lookback",
        type=int,
        default=8,
        help="Years to look back for year-based fallback (default: 8)",
    )
    parser.add_argument(
        "--freshness_days",
        type=int,
        default=90,
        help="Per-pair shard gate: skip (skill, occupation) pairs with any "
             "book_search_attempts row newer than N days (default: 90). "
             "Independent from the per-source 1-day check inside the main loop.",
    )
    parser.add_argument(
        "--shard",
        type=str,
        default=None,
        help="Filter pairs by ISCO group prefix on occupations.isco_code. "
             "Single digit (e.g. 2), two digits (e.g. 21), or comma list of one type "
             "(21,22). Mixing single- and two-digit values is rejected.",
    )
    parser.add_argument(
        "--max-pairs",
        type=int,
        default=None,
        help="Stop cleanly after N (skill, occupation) pairs have been processed. "
             "Stops between pairs so book_search_attempts stays consistent.",
    )
    parser.add_argument(
        "--fill-gaps-only",
        action="store_true",
        help="Only search skills with zero books (gap-filling mode)",
    )
    parser.add_argument(
        "--featured-only",
        action="store_true",
        help="Only search skills for featured occupations",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results without saving to DB",
    )
    parser.add_argument(
        "--semantic-model",
        type=str,
        choices=["cohere", "cohere_embed"],
        default="cohere",
        help="Semantic model: 'cohere' (rerank, best) or 'cohere_embed' (embed, legacy)",
    )
    args = parser.parse_args()

    # Validate --shard early so a typo fails fast before any DB work.
    try:
        shard_prefix_length, shard_values = _parse_shard(args.shard)
    except ValueError as e:
        parser.error(str(e))

    # Calculate dynamic min_year if not explicitly provided
    if args.min_year is None:
        args.min_year = datetime.utcnow().year - args.primary_years_lookback
        print(f"Using dynamic min_year: {args.min_year} (current_year - {args.primary_years_lookback})")

    # Calculate fallback year threshold
    args.fallback_min_year = datetime.utcnow().year - args.fallback_years_lookback

    # Gap-filling mode uses even older threshold
    if args.fill_gaps_only:
        args.min_year = datetime.utcnow().year - 10
        print(f"Gap-filling mode: using min_year={args.min_year} (current_year - 10)")

    # Set the semantic model before running search
    set_semantic_model(args.semantic_model)
    print(f"Using semantic model: {args.semantic_model}")

    results = run_search(
        skill_limit=args.skill_limit,
        book_limit=args.book_limit,
        min_year=args.min_year,
        fallback_min_year=args.fallback_min_year,
        force_refresh=args.force_refresh,
        max_age_days=1,  # per-source check inside the main loop is unrelated to --freshness_days
        featured_only=args.featured_only,
        fill_gaps_only=args.fill_gaps_only,
        dry_run=args.dry_run,
        shard_prefix_length=shard_prefix_length,
        shard_values=shard_values,
        freshness_days=args.freshness_days,
        max_pairs=args.max_pairs,
    )
