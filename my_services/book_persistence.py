def upsert_book(conn, book):
    """
    Upsert a book by ISBN. Checks for existing book by isbn_10 OR isbn_13 first,
    then updates or inserts accordingly.
    
    Also saves free_access info if available.
    """
    # Extract free access info if present
    free_access = book.get("free_access") or {}
    free_access_type = free_access.get("type")  # 'free' or 'preview'
    free_access_url = free_access.get("read_url")
    free_access_epub = free_access.get("epub_available", False)
    free_access_pdf = free_access.get("pdf_available", False)
    
    # Check if free_access columns exist
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'books' AND column_name = 'free_access_type'
        """)
        has_free_columns = cur.fetchone() is not None
    
    # First, check if book already exists by either ISBN
    find_sql = """
    SELECT id FROM books
    WHERE (isbn_10 IS NOT NULL AND isbn_10 = %(isbn_10)s)
       OR (isbn_13 IS NOT NULL AND isbn_13 = %(isbn_13)s)
    LIMIT 1;
    """

    with conn.cursor() as cur:
        cur.execute(find_sql, book)
        existing = cur.fetchone()

        if existing:
            # Update existing book
            book_id = existing[0]
            if has_free_columns:
                update_sql = """
                UPDATE books SET
                    title = %(title)s,
                    authors = %(authors)s,
                    published_year = %(published_year)s,
                    description = %(description)s,
                    free_access_type = %(free_access_type)s,
                    free_access_url = %(free_access_url)s,
                    free_access_source = %(free_access_source)s,
                    free_access_epub = %(free_access_epub)s,
                    free_access_pdf = %(free_access_pdf)s,
                    updated_at = NOW()
                WHERE id = %(book_id)s
                RETURNING id;
                """
            else:
                update_sql = """
                UPDATE books SET
                    title = %(title)s,
                    authors = %(authors)s,
                    published_year = %(published_year)s,
                    description = %(description)s,
                    updated_at = NOW()
                WHERE id = %(book_id)s
                RETURNING id;
                """
            update_params = {
                **book,
                "book_id": book_id,
                "free_access_type": free_access_type,
                "free_access_url": free_access_url,
                "free_access_source": book.get("source") if free_access_type else None,
                "free_access_epub": free_access_epub,
                "free_access_pdf": free_access_pdf,
            }
            cur.execute(update_sql, update_params)
            return cur.fetchone()[0]
        else:
            # Insert new book
            if has_free_columns:
                insert_sql = """
                INSERT INTO books (
                    isbn_10,
                    isbn_13,
                    title,
                    authors,
                    published_year,
                    language_code,
                    description,
                    thumbnail,
                    source,
                    free_access_type,
                    free_access_url,
                    free_access_source,
                    free_access_epub,
                    free_access_pdf,
                    created_at,
                    updated_at
                )
                VALUES (
                    %(isbn_10)s,
                    %(isbn_13)s,
                    %(title)s,
                    %(authors)s,
                    %(published_year)s,
                    %(language_code)s,
                    %(description)s,
                    %(thumbnail)s,
                    %(source)s,
                    %(free_access_type)s,
                    %(free_access_url)s,
                    %(free_access_source)s,
                    %(free_access_epub)s,
                    %(free_access_pdf)s,
                    NOW(),
                    NOW()
                )
                RETURNING id;
                """
            else:
                insert_sql = """
                INSERT INTO books (
                    isbn_10,
                    isbn_13,
                    title,
                    authors,
                    published_year,
                    language_code,
                    description,
                    thumbnail,
                    source,
                    created_at,
                    updated_at
                )
                VALUES (
                    %(isbn_10)s,
                    %(isbn_13)s,
                    %(title)s,
                    %(authors)s,
                    %(published_year)s,
                    %(language_code)s,
                    %(description)s,
                    %(thumbnail)s,
                    %(source)s,
                    NOW(),
                    NOW()
                )
                RETURNING id;
                """
            insert_params = {
                **book,
                "free_access_type": free_access_type,
                "free_access_url": free_access_url,
                "free_access_source": book.get("source") if free_access_type else None,
                "free_access_epub": free_access_epub,
                "free_access_pdf": free_access_pdf,
            }
            cur.execute(insert_sql, insert_params)
            return cur.fetchone()[0]


def link_book_to_skill(conn, skill_uri: str, occupation_uri: str, book_id: int, rank: int, fallback_tier: int = 0, score: float = 0.0):
    """
    Link a book to a skill-occupation pair.

    Args:
        fallback_tier: Search strategy used (0=primary, 1=occupation, 2=year, 3=broader, 4=broader+year)
        score: Composite ranking score from BookRanker (higher = better)
    """
    # Ensure score column exists
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'skill_book_matches' AND column_name = 'score'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE skill_book_matches ADD COLUMN score FLOAT DEFAULT 0.0")
            conn.commit()

    sql = """
    INSERT INTO skill_book_matches (skill_uri, occupation_uri, book_id, rank, fallback_tier, score, matched_at)
    VALUES (%s, %s, %s, %s, %s, %s, NOW())
    ON CONFLICT (skill_uri, occupation_uri, book_id)
    DO UPDATE SET
        rank = EXCLUDED.rank,
        fallback_tier = EXCLUDED.fallback_tier,
        score = EXCLUDED.score,
        matched_at = NOW();
    """
    with conn.cursor() as cur:
        cur.execute(sql, (skill_uri, occupation_uri, book_id, rank, fallback_tier, score))
