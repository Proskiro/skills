def upsert_book(conn, book):
    """
    Upsert a book by ISBN. Checks for existing book by isbn_10 OR isbn_13 first,
    then updates or inserts accordingly.
    """
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
            update_params = {**book, "book_id": book_id}
            cur.execute(update_sql, update_params)
            return cur.fetchone()[0]
        else:
            # Insert new book
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
            cur.execute(insert_sql, book)
            return cur.fetchone()[0]


def link_book_to_skill(conn, skill_uri: str, occupation_uri: str, book_id: int, rank: int):
    sql = """
    INSERT INTO skill_book_matches (skill_uri, occupation_uri, book_id, rank, matched_at)
    VALUES (%s, %s, %s, %s, NOW())
    ON CONFLICT (skill_uri, occupation_uri, book_id)
    DO UPDATE SET
        rank = EXCLUDED.rank,
        matched_at = NOW();
    """

    with conn.cursor() as cur:
        cur.execute(sql, (skill_uri, occupation_uri, book_id, rank))
