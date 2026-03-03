"""
Load O*NET alternate titles into the occupations table.

Supports two modes:
  1. Legacy (default): reads the ISCO-grouped CSV produced by isco_to_onet_alt_titles.py
     and updates all occupations within each ISCO group with the same alt titles.
  2. Matched (--matched): reads the per-occupation CSV produced by match_onet_to_esco.py
     and updates each ESCO occupation with only its semantically matched alt titles.

Usage:
    # Legacy mode (ISCO-group level):
    python scripts/load_onet_alt_titles.py --env .env.dev --dry-run
    python scripts/load_onet_alt_titles.py --env .env.dev

    # Matched mode (per-occupation, recommended):
    python scripts/load_onet_alt_titles.py --env .env.dev --matched --dry-run
    python scripts/load_onet_alt_titles.py --env .env.dev --matched
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
MATCHED_CSV_PATH = SCRIPT_DIR / "output" / "esco_onet_matched_titles.csv"


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


def load_matched_csv(path: Path) -> dict[str, str]:
    """Read the per-occupation matched CSV and return {esco_uri: alt_titles_text}."""
    result = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            uri = row["esco_uri"].strip()
            alt_titles = row["alt_titles"].strip()
            if uri and alt_titles:
                result[uri] = alt_titles
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Load O*NET alternate titles into occupations DB")
    parser.add_argument("--env", required=True, help="Path to .env file (e.g. .env.dev or .env)")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing to DB")
    parser.add_argument("--matched", action="store_true",
                        help="Use per-occupation matched CSV from match_onet_to_esco.py instead of ISCO-grouped CSV")
    args = parser.parse_args()

    env_path = Path(args.env)
    if not env_path.is_absolute():
        env_path = SKILLS_DIR / env_path
    if not env_path.exists():
        logger.error("Env file not found: %s", env_path)
        sys.exit(1)

    if args.matched:
        _load_matched(env_path, args.dry_run)
    else:
        _load_legacy(env_path, args.dry_run)


def _load_matched(env_path: Path, dry_run: bool) -> None:
    """Load alt titles per individual ESCO occupation using semantic matching output."""
    if not MATCHED_CSV_PATH.exists():
        logger.error("Matched CSV not found: %s", MATCHED_CSV_PATH)
        logger.error("Run match_onet_to_esco.py first to generate it.")
        sys.exit(1)

    logger.info("Reading %s ...", MATCHED_CSV_PATH)
    uri_titles = load_matched_csv(MATCHED_CSV_PATH)
    logger.info("Loaded %d ESCO occupations with matched alt titles", len(uri_titles))

    conn = get_connection(env_path)
    cur = conn.cursor()

    try:
        cur.execute("ALTER TABLE occupations ADD COLUMN IF NOT EXISTS onet_alt_titles TEXT;")
        conn.commit()

        # First, clear all existing alt titles so we don't leave stale data
        if not dry_run:
            cur.execute("UPDATE occupations SET onet_alt_titles = NULL")
            logger.info("Cleared existing onet_alt_titles")

        updated = 0
        not_found = 0

        for uri, titles_text in sorted(uri_titles.items()):
            if dry_run:
                cur.execute("SELECT preferred_title FROM occupations WHERE uri = %s", (uri,))
                row = cur.fetchone()
                if row:
                    n_titles = len(titles_text.split("\n"))
                    logger.info("[DRY RUN] %s → %d alt titles", row[0], n_titles)
                    updated += 1
                else:
                    not_found += 1
            else:
                cur.execute(
                    "UPDATE occupations SET onet_alt_titles = %s WHERE uri = %s",
                    (titles_text, uri),
                )
                if cur.rowcount:
                    updated += cur.rowcount
                else:
                    not_found += 1

        if not dry_run:
            conn.commit()
            logger.info("Committed changes")

        print(f"\n{'DRY RUN ' if dry_run else ''}SUMMARY (matched mode)")
        print("=" * 40)
        print(f"ESCO occupations in CSV:   {len(uri_titles)}")
        print(f"Occupations updated:       {updated}")
        print(f"URIs not found in DB:      {not_found}")

    except Exception:
        conn.rollback()
        logger.exception("Error during update")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


def _load_legacy(env_path: Path, dry_run: bool) -> None:
    """Load alt titles at ISCO-group level (original behavior)."""
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

            if dry_run:
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

        if not dry_run:
            conn.commit()
            logger.info("Committed changes")

        print(f"\n{'DRY RUN ' if dry_run else ''}SUMMARY")
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
