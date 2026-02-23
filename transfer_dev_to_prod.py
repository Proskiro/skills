"""
Transfer books, skill_book_matches, and book_search_attempts from dev DB to prod DB.

Reads all rows from dev (read-only) and upserts into prod (overwriting matches).
Dev database is NOT modified.

Books are matched by (source, external_id) — dev IDs are NOT carried over.
skill_book_matches.book_id is remapped to the corresponding prod book ID.

Usage:
    python transfer_dev_to_prod.py          # dry-run (shows counts, no writes)
    python transfer_dev_to_prod.py --apply  # actually write to prod
"""

import argparse
import psycopg2
from psycopg2.extras import execute_values, Json

SSL_CERT = "/Users/esa/Desktop/side_projects/proskiro/global-bundle.pem"
DB_USER = "adminskillsdb"
DB_PASSWORD = "Profession-skills-c0urse"
DB_NAME = "skillsdb"
DB_PORT = "5432"

DEV_HOST = "skillsdb-dev.cxq4ookmeq59.eu-west-2.rds.amazonaws.com"
PROD_HOST = "skillsdb.cxq4ookmeq59.eu-west-2.rds.amazonaws.com"


def connect(host):
    return psycopg2.connect(
        host=host,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        sslmode="verify-full",
        sslrootcert=SSL_CERT,
    )


def fetch_all(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {table}")
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        # Wrap any dict values with Json() so psycopg2 can serialize them
        rows = [
            tuple(Json(v) if isinstance(v, dict) else v for v in row)
            for row in rows
        ]
    return columns, rows


def upsert_books(prod_conn, columns, rows):
    """
    Upsert books — conflict on (source, external_id), overwrite all other columns.
    Drops the 'id' column so prod generates its own IDs.
    Returns a mapping of dev_book_id -> prod_book_id.
    """
    id_idx = columns.index("id")
    # Find source and external_id indices for building the ID mapping
    source_idx = columns.index("source")
    ext_id_idx = columns.index("external_id")

    # Strip 'id' from columns and rows
    insert_cols = [c for i, c in enumerate(columns) if i != id_idx]
    insert_rows = [
        tuple(v for i, v in enumerate(row) if i != id_idx)
        for row in rows
    ]

    conflict_keys = {"source", "external_id"}
    update_cols = [c for c in insert_cols if c not in conflict_keys]

    col_list = ", ".join(insert_cols)
    conflict_list = ", ".join(conflict_keys)
    update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)

    sql = f"""
    INSERT INTO books ({col_list})
    VALUES %s
    ON CONFLICT ({conflict_list})
    DO UPDATE SET {update_set}
    RETURNING id, source, external_id;
    """

    with prod_conn.cursor() as cur:
        execute_values(cur, sql, insert_rows, page_size=500, fetch=True)
        prod_results = cur.fetchall()  # (prod_id, source, external_id)
    prod_conn.commit()

    # Build lookup: (source, external_id) -> prod_id
    prod_lookup = {(src, ext): pid for pid, src, ext in prod_results}

    # Build mapping: dev_id -> prod_id
    id_map = {}
    for row in rows:
        dev_id = row[id_idx]
        source = row[source_idx]
        ext_id = row[ext_id_idx]
        # Unwrap Json objects for lookup
        source_val = source.adapted if isinstance(source, Json) else source
        ext_id_val = ext_id.adapted if isinstance(ext_id, Json) else ext_id
        prod_id = prod_lookup.get((source_val, ext_id_val))
        if prod_id:
            id_map[dev_id] = prod_id

    return id_map


def upsert_skill_book_matches(prod_conn, columns, rows, id_map):
    """
    Upsert skill_book_matches — conflict on (skill_uri, occupation_uri, book_id).
    Remaps book_id from dev IDs to prod IDs using id_map.
    Drops the 'id' column if present.
    """
    book_id_idx = columns.index("book_id")

    # Drop 'id' column if it exists
    id_idx = columns.index("id") if "id" in columns else None

    # Remap book_id and filter out rows with no mapping
    remapped_rows = []
    skipped = 0
    for row in rows:
        dev_book_id = row[book_id_idx]
        prod_book_id = id_map.get(dev_book_id)
        if prod_book_id is None:
            skipped += 1
            continue
        new_row = list(row)
        new_row[book_id_idx] = prod_book_id
        remapped_rows.append(tuple(new_row))

    if skipped:
        print(f"  -> skipped {skipped} rows (no matching book in prod)")

    # Strip 'id' column if present
    if id_idx is not None:
        insert_cols = [c for i, c in enumerate(columns) if i != id_idx]
        insert_rows = [
            tuple(v for i, v in enumerate(row) if i != id_idx)
            for row in remapped_rows
        ]
    else:
        insert_cols = columns
        insert_rows = remapped_rows

    conflict_keys = {"skill_uri", "occupation_uri", "book_id"}
    update_cols = [c for c in insert_cols if c not in conflict_keys]

    col_list = ", ".join(insert_cols)
    conflict_list = ", ".join(conflict_keys)
    update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)

    sql = f"""
    INSERT INTO skill_book_matches ({col_list})
    VALUES %s
    ON CONFLICT ({conflict_list})
    DO UPDATE SET {update_set};
    """

    with prod_conn.cursor() as cur:
        execute_values(cur, sql, insert_rows, page_size=500)
    prod_conn.commit()


def upsert_book_search_attempts(prod_conn, columns, rows):
    """Upsert book_search_attempts — conflict on (skill_uri, occupation_uri, source)."""
    conflict_keys = {"skill_uri", "occupation_uri", "source"}
    update_cols = [c for c in columns if c not in conflict_keys]

    col_list = ", ".join(columns)
    conflict_list = ", ".join(conflict_keys)
    update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)

    sql = f"""
    INSERT INTO book_search_attempts ({col_list})
    VALUES %s
    ON CONFLICT ({conflict_list})
    DO UPDATE SET {update_set};
    """

    with prod_conn.cursor() as cur:
        execute_values(cur, sql, rows, page_size=500)
    prod_conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Transfer dev book data to prod")
    parser.add_argument("--apply", action="store_true", help="Actually write to prod (default is dry-run)")
    args = parser.parse_args()

    print("Connecting to dev...")
    dev_conn = connect(DEV_HOST)

    print("Reading books from dev...")
    books_cols, books_rows = fetch_all(dev_conn, "books")
    print(f"  -> {len(books_rows)} books")

    print("Reading skill_book_matches from dev...")
    sbm_cols, sbm_rows = fetch_all(dev_conn, "skill_book_matches")
    print(f"  -> {len(sbm_rows)} skill_book_matches")

    print("Reading book_search_attempts from dev...")
    bsa_cols, bsa_rows = fetch_all(dev_conn, "book_search_attempts")
    print(f"  -> {len(bsa_rows)} book_search_attempts")

    dev_conn.close()

    if not args.apply:
        print("\nDry-run complete. Use --apply to write to prod.")
        return

    print("\nConnecting to prod...")
    prod_conn = connect(PROD_HOST)

    print("Upserting books...")
    id_map = upsert_books(prod_conn, books_cols, books_rows)
    print(f"  -> done ({len(id_map)} books mapped)")

    print("Upserting skill_book_matches...")
    upsert_skill_book_matches(prod_conn, sbm_cols, sbm_rows, id_map)
    print("  -> done")

    print("Upserting book_search_attempts...")
    upsert_book_search_attempts(prod_conn, bsa_cols, bsa_rows)
    print("  -> done")

    prod_conn.close()
    print("\nTransfer complete!")


if __name__ == "__main__":
    main()
