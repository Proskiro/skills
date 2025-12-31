def upsert_book(conn, book):
    sql = """
    INSERT INTO books (
        isbn_10,
        isbn_13,
        title,
        authors,
        published_year,
        language_code,
        description,
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
        %(source)s,
        NOW(),
        NOW()
    )
    ON CONFLICT (isbn_13)
    DO UPDATE SET
        title = EXCLUDED.title,
        authors = EXCLUDED.authors,
        published_year = EXCLUDED.published_year,
        description = EXCLUDED.description,
        updated_at = NOW()
    RETURNING id;
    """

    with conn.cursor() as cur:
        cur.execute(sql, book)
        return cur.fetchone()[0]


def link_book_to_skill(conn, skill_uri: str, book_id: int, rank: int):
    sql = """
    INSERT INTO skill_book_matches (skill_uri, book_id, rank, matched_at)
    VALUES (%s, %s, %s, NOW())
    ON CONFLICT (skill_uri, book_id)
    DO UPDATE SET
        rank = EXCLUDED.rank,
        matched_at = NOW();
    """

    with conn.cursor() as cur:
        cur.execute(sql, (skill_uri, book_id, rank))
