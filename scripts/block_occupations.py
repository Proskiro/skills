"""
Block occupations in the database based on content_filter.py keywords.

Adds an `is_blocked` column to the `occupations` table and sets it to TRUE
for any occupation whose preferred_title matches EXCLUDED_OCCUPATION_KEYWORDS
or whose URI is in EXCLUDED_OCCUPATION_URIS.

This is a backfill script to apply the new content filtering logic to existing occupations 
in the database. It can be safely re-run multiple times (idempotent) and includes a 
dry-run mode for previewing changes without applying them.

Usage:
    python scripts/block_occupations.py --env .env.dev --dry-run   # preview
    python scripts/block_occupations.py --env .env.dev              # apply to dev
    python scripts/block_occupations.py --env .env                  # apply to production
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_DIR = SCRIPT_DIR.parent

sys.path.insert(0, str(SKILLS_DIR))
from my_services.content_filter import EXCLUDED_OCCUPATION_KEYWORDS, EXCLUDED_OCCUPATION_URIS

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def get_connection(env_file: Path):
    load_dotenv(env_file, override=True)
    host = os.getenv("POSTGRES_HOST")
    db_name = os.getenv("POSTGRES_DB")
    logger.info("Connecting to %s @ %s", db_name, host)
    ssl_cert = os.getenv("SSL_CERT_PATH", str(SKILLS_DIR / "global-bundle.pem"))
    conn = psycopg2.connect(
        host=host,
        port=os.getenv("POSTGRES_PORT"),
        dbname=db_name,
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        sslmode="verify-full",
        sslrootcert=ssl_cert,
    )
    conn.autocommit = False
    return conn


def main() -> None:
    parser = argparse.ArgumentParser(description="Block occupations based on content filter")
    parser.add_argument("--env", required=True, help="Path to .env file (e.g. .env.dev or .env)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
    args = parser.parse_args()

    env_path = Path(args.env)
    if not env_path.is_absolute():
        env_path = SKILLS_DIR / env_path
    if not env_path.exists():
        logger.error("Env file not found: %s", env_path)
        sys.exit(1)

    conn = get_connection(env_path)
    cur = conn.cursor()

    try:
        # Ensure the column exists
        cur.execute("""
            ALTER TABLE occupations
            ADD COLUMN IF NOT EXISTS is_blocked BOOLEAN NOT NULL DEFAULT FALSE;
        """)
        conn.commit()
        logger.info("Ensured is_blocked column exists")

        # Reset all to unblocked first (so re-running is idempotent)
        if not args.dry_run:
            cur.execute("UPDATE occupations SET is_blocked = FALSE;")
            logger.info("Reset all is_blocked to FALSE")

        # Build keyword ILIKE conditions
        keyword_conditions = " OR ".join(
            f"preferred_title ILIKE %s" for _ in EXCLUDED_OCCUPATION_KEYWORDS
        )
        keyword_values = [f"%{kw}%" for kw in EXCLUDED_OCCUPATION_KEYWORDS]

        # Build URI IN condition
        uri_condition = ""
        uri_values = []
        if EXCLUDED_OCCUPATION_URIS:
            placeholders = ", ".join("%s" for _ in EXCLUDED_OCCUPATION_URIS)
            uri_condition = f"OR uri IN ({placeholders})"
            uri_values = list(EXCLUDED_OCCUPATION_URIS)

        where_clause = f"WHERE ({keyword_conditions}) {uri_condition}"
        all_values = keyword_values + uri_values

        if args.dry_run:
            cur.execute(
                f"SELECT uri, preferred_title FROM occupations {where_clause} ORDER BY preferred_title",
                all_values,
            )
            rows = cur.fetchall()
            print(f"\nDRY RUN — occupations that would be blocked ({len(rows)}):\n")
            for uri, title in rows:
                print(f"  {title}")
                print(f"    {uri}")
        else:
            cur.execute(
                f"UPDATE occupations SET is_blocked = TRUE {where_clause}",
                all_values,
            )
            blocked = cur.rowcount
            conn.commit()
            logger.info("Committed changes")

            print(f"\nSUMMARY")
            print("=" * 40)
            print(f"Occupations blocked: {blocked}")

    except Exception:
        conn.rollback()
        logger.exception("Error during update")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
