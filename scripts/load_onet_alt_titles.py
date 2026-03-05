"""
Load semantically-filtered O*NET alternate titles into the occupations table.

Reads the per-occupation matched CSV produced by match_onet_to_esco.py and
updates the `onet_alt_titles` column for each individual occupation by URI.

This ensures each occupation only gets alt titles that were semantically
matched to it — not every title in the ISCO group.

Pipeline:
    1. isco_to_onet_alt_titles.py   → raw ISCO→O*NET crosswalk
    2. match_onet_to_esco.py        → per-occupation semantic filtering
    3. load_onet_alt_titles.py      → load filtered titles into DB  (this script)

Usage:
    python scripts/load_onet_alt_titles.py --env .env.dev --dry-run
    python scripts/load_onet_alt_titles.py --env .env.dev
    python scripts/load_onet_alt_titles.py --env .env
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_DIR = SCRIPT_DIR.parent
CSV_PATH = SCRIPT_DIR / "output" / "esco_onet_matched_titles.csv"


def get_connection(env_file: Path):
    """Create a psycopg2 connection using the specified .env file."""
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


def load_matched_csv(path: Path) -> dict[str, str]:
    """
    Read the per-occupation matched CSV.

    Returns: {esco_uri: newline-separated alt titles string}
    """
    occupations: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            uri = row["esco_uri"].strip()
            alt_titles = row["alt_titles"].strip()
            if uri:
                occupations[uri] = alt_titles
    return occupations


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load semantically-filtered O*NET alternate titles into occupations DB"
    )
    parser.add_argument("--env", required=True, help="Path to .env file (e.g. .env.dev or .env)")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing to DB")
    args = parser.parse_args()

    env_path = Path(args.env)
    if not env_path.is_absolute():
        env_path = SKILLS_DIR / env_path
    if not env_path.exists():
        logger.error("Env file not found: %s", env_path)
        sys.exit(1)

    if not CSV_PATH.exists():
        logger.error("CSV not found: %s", CSV_PATH)
        logger.error("Run match_onet_to_esco.py first to generate it.")
        sys.exit(1)

    logger.info("Reading %s ...", CSV_PATH)
    occupations = load_matched_csv(CSV_PATH)
    with_titles = sum(1 for t in occupations.values() if t)
    logger.info(
        "Loaded %d occupations (%d with alt titles, %d without)",
        len(occupations), with_titles, len(occupations) - with_titles,
    )

    conn = get_connection(env_path)
    cur = conn.cursor()

    try:
        # Ensure the column exists
        cur.execute("""
            ALTER TABLE occupations
            ADD COLUMN IF NOT EXISTS onet_alt_titles TEXT;
        """)
        conn.commit()
        logger.info("Ensured onet_alt_titles column exists")

        updated = 0
        cleared = 0
        not_found = 0

        for uri, alt_titles in sorted(occupations.items()):
            # Store titles or NULL if no titles matched
            titles_value = alt_titles if alt_titles else None

            if args.dry_run:
                cur.execute(
                    "SELECT preferred_title FROM occupations WHERE uri = %s",
                    (uri,),
                )
                row = cur.fetchone()
                if row:
                    title_count = len(alt_titles.split("\n")) if alt_titles else 0
                    logger.info(
                        "[DRY RUN] %s → %d alt titles",
                        row[0], title_count,
                    )
                    if alt_titles:
                        updated += 1
                    else:
                        cleared += 1
                else:
                    not_found += 1
            else:
                cur.execute(
                    """
                    UPDATE occupations
                    SET onet_alt_titles = %s
                    WHERE uri = %s
                    """,
                    (titles_value, uri),
                )
                if cur.rowcount:
                    if alt_titles:
                        updated += 1
                    else:
                        cleared += 1
                else:
                    not_found += 1

        if not args.dry_run:
            conn.commit()
            logger.info("Committed changes")

        print(f"\n{'DRY RUN ' if args.dry_run else ''}SUMMARY")
        print("=" * 40)
        print(f"Occupations in CSV:          {len(occupations)}")
        print(f"Updated with alt titles:     {updated}")
        print(f"Cleared (no matched titles): {cleared}")
        print(f"Not found in DB:             {not_found}")

    except Exception:
        conn.rollback()
        logger.exception("Error during update")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
