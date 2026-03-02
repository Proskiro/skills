"""
Load O*NET alternate titles into the occupations table.

Reads the ISCO → O*NET alternate-titles CSV produced by
isco_to_onet_alt_titles.py and bulk-updates the `onet_alt_titles` column
in the `occupations` table grouped by ISCO code.

Usage:
    python scripts/load_onet_alt_titles.py --env .env.dev --dry-run   # preview against dev
    python scripts/load_onet_alt_titles.py --env .env.dev              # load into dev
    python scripts/load_onet_alt_titles.py --env .env                  # load into production
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_DIR = SCRIPT_DIR.parent
CSV_PATH = SCRIPT_DIR / "output" / "isco_onet_alternate_titles.csv"


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


def load_csv(path: Path) -> dict[str, list[str]]:
    """Read the CSV and group deduplicated alternate titles by ISCO group."""
    groups: dict[str, set[str]] = defaultdict(set)
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            isco = row["iscoGroup"].strip()
            title = row["alternateTitle"].strip()
            if isco and title:
                groups[isco].add(title)

    return {isco: sorted(titles) for isco, titles in groups.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Load O*NET alternate titles into occupations DB")
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
        logger.error("Run isco_to_onet_alt_titles.py first to generate it.")
        sys.exit(1)

    logger.info("Reading %s ...", CSV_PATH)
    groups = load_csv(CSV_PATH)
    logger.info("Loaded %d ISCO groups with alternate titles", len(groups))

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
        skipped = 0

        for isco, titles in sorted(groups.items()):
            isco_code = f"C{isco}"
            titles_text = "\n".join(titles)

            if args.dry_run:
                # Check how many occupations would be affected
                cur.execute(
                    "SELECT COUNT(*) FROM occupations WHERE isco_code LIKE %s",
                    (f"{isco_code}%",),
                )
                count = cur.fetchone()[0]
                if count:
                    logger.info(
                        "[DRY RUN] %s → %d occupations, %d alternate titles",
                        isco_code, count, len(titles),
                    )
                    updated += count
                else:
                    skipped += 1
            else:
                cur.execute(
                    """
                    UPDATE occupations
                    SET onet_alt_titles = %s
                    WHERE isco_code LIKE %s
                    """,
                    (titles_text, f"{isco_code}%"),
                )
                rows = cur.rowcount
                if rows:
                    updated += rows
                else:
                    skipped += 1

        if not args.dry_run:
            conn.commit()
            logger.info("Committed changes")

        print(f"\n{'DRY RUN ' if args.dry_run else ''}SUMMARY")
        print("=" * 40)
        print(f"ISCO groups in CSV:        {len(groups)}")
        print(f"Occupations updated:       {updated}")
        print(f"ISCO groups with no match: {skipped}")

    except Exception:
        conn.rollback()
        logger.exception("Error during update")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
