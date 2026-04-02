import psycopg2
from psycopg2.extras import execute_values


def save_books(conn, books):
    """
    Insert many normalised book records into the books table.
    Ignores duplicates automatically (ON CONFLICT DO NOTHING).
    """

    sql = """
    INSERT INTO books (
        source, external_id, isbn_10, isbn_13, title, authors,
        description, language_code, published_year
    )
    VALUES %s
    ON CONFLICT (source, external_id) DO NOTHING;
    """

    values = [
        (
            b["source"],
            b["external_id"],
            b["isbn_10"],
            b["isbn_13"],
            b["title"],
            b["authors"],
            b["description"],
            b["language_code"],
            b["published_year"],
        )
        for b in books
    ]

    with conn.cursor() as cur:
        execute_values(cur, sql, values)
    conn.commit()
