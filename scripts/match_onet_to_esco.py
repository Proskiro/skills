"""
Semantic matching: ESCO occupations → O*NET-SOC codes within each ISCO group.

Uses Cohere embeddings to match each ESCO occupation (title + description)
to the most relevant O*NET-SOC code(s) within its ISCO group. Then assigns
only the alternate titles from matched O*NET-SOC codes to each ESCO occupation.

This replaces the coarse ISCO-level grouping with per-occupation matching.

Usage:
    python scripts/match_onet_to_esco.py --env .env.dev --dry-run
    python scripts/match_onet_to_esco.py --env .env.dev --threshold 0.4
    python scripts/match_onet_to_esco.py --env .env.dev

Output:
    scripts/output/esco_onet_matched_titles.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import cohere
import numpy as np
import psycopg2
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_DIR = SCRIPT_DIR.parent
DATA_DIR = SCRIPT_DIR / "data"
OUTPUT_DIR = SCRIPT_DIR / "output"

# Input files
CROSSWALK_CSV = OUTPUT_DIR / "isco_onet_alternate_titles.csv"
ONET_OCCUPATION_DATA = DATA_DIR / "Occupation_Data.txt"

# Output file
OUTPUT_CSV = OUTPUT_DIR / "esco_onet_matched_titles.csv"

# Cohere settings
EMBED_MODEL = "embed-english-v3.0"
EMBED_INPUT_TYPE = "search_document"
EMBED_BATCH_SIZE = 96  # Cohere's max batch size


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_connection(env_file: Path):
    """Create a psycopg2 connection using the specified .env file."""
    load_dotenv(env_file, override=True)
    host = os.getenv("POSTGRES_HOST")
    db_name = os.getenv("POSTGRES_DB")
    logger.info("Connecting to %s @ %s", db_name, host)
    ssl_cert = os.getenv("SSL_ROOTCERT", os.getenv("SSL_CERT_PATH", str(SKILLS_DIR / "global-bundle.pem")))
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


def load_esco_occupations(conn) -> dict[str, list[dict]]:
    """
    Load ESCO occupations from DB, grouped by 4-digit ISCO code.

    Returns: {isco_4digit: [{uri, isco_code, title, description}, ...]}
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT uri, isco_code, preferred_title, description
        FROM occupations
        WHERE isco_code IS NOT NULL
          AND preferred_title IS NOT NULL
          AND status != 'obsolete'
        ORDER BY isco_code
    """)
    rows = cur.fetchall()
    cur.close()

    groups: dict[str, list[dict]] = defaultdict(list)
    for uri, isco_code, title, description in rows:
        # Extract 4-digit ISCO: "C5164.1.1" -> "5164"
        isco_4 = isco_code[1:5] if isco_code.startswith("C") else isco_code[:4]
        groups[isco_4].append({
            "uri": uri,
            "isco_code": isco_code,
            "title": title,
            "description": description or "",
        })

    logger.info("Loaded %d ESCO occupations in %d ISCO groups", sum(len(v) for v in groups.values()), len(groups))
    return dict(groups)


# ---------------------------------------------------------------------------
# O*NET data loading
# ---------------------------------------------------------------------------

def load_onet_descriptions(path: Path) -> dict[str, dict]:
    """
    Load O*NET occupation descriptions.

    Returns: {onet_soc_code: {title, description}}
    """
    result = {}
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            code = row["O*NET-SOC Code"].strip()
            title = row["Title"].strip()
            desc = row["Description"].strip()
            result[code] = {"title": title, "description": desc}
    logger.info("Loaded %d O*NET occupation descriptions", len(result))
    return result


def load_crosswalk(path: Path) -> dict[str, dict[str, list[str]]]:
    """
    Load ISCO → O*NET-SOC → alternate titles crosswalk.

    Returns: {isco_4digit: {onet_soc_code: [alt_title, ...]}}
    """
    groups: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            isco = row["iscoGroup"].strip()
            onet = row["onetSocCode"].strip()
            title = row["alternateTitle"].strip()
            if isco and onet and title:
                groups[isco][onet].append(title)
    logger.info("Loaded crosswalk: %d ISCO groups, %d O*NET-SOC codes",
                len(groups), sum(len(v) for v in groups.values()))
    return dict(groups)


# ---------------------------------------------------------------------------
# Embedding & matching
# ---------------------------------------------------------------------------

def get_cohere_client() -> cohere.Client:
    """Create Cohere client from environment."""
    api_key = os.getenv("COHERE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "COHERE_API_KEY environment variable is not set. "
            "Add it to your .env.dev file."
        )
    return cohere.Client(api_key)


def embed_texts(client: cohere.Client, texts: list[str]) -> np.ndarray:
    """
    Embed a list of texts using Cohere, handling batching.

    Returns: numpy array of shape (len(texts), embedding_dim)
    """
    all_embeddings = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i:i + EMBED_BATCH_SIZE]
        response = client.embed(
            texts=batch,
            model=EMBED_MODEL,
            input_type=EMBED_INPUT_TYPE,
        )
        all_embeddings.extend(response.embeddings)
        if i + EMBED_BATCH_SIZE < len(texts):
            time.sleep(0.5)  # Rate limit courtesy
    return np.array(all_embeddings)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Compute cosine similarity between two sets of vectors.

    Args:
        a: shape (m, d)
        b: shape (n, d)

    Returns: shape (m, n) similarity matrix
    """
    a_norm = a / np.linalg.norm(a, axis=1, keepdims=True)
    b_norm = b / np.linalg.norm(b, axis=1, keepdims=True)
    return a_norm @ b_norm.T


def make_text(title: str, description: str) -> str:
    """Combine title and description for embedding."""
    if description:
        return f"{title}: {description[:500]}"
    return title


def match_isco_group(
    client: cohere.Client,
    esco_occs: list[dict],
    onet_codes: dict[str, list[str]],
    onet_descriptions: dict[str, dict],
    threshold: float,
) -> list[dict]:
    """
    Match ESCO occupations to O*NET-SOC codes within a single ISCO group.

    Returns list of {esco_uri, isco_code, matched_onet_socs, alt_titles}
    """
    # Filter O*NET codes to those we have descriptions for
    valid_onet = {}
    for code, alt_titles in onet_codes.items():
        if code in onet_descriptions:
            valid_onet[code] = alt_titles

    if not valid_onet:
        # No O*NET descriptions available — skip this group
        return []

    # Build texts for embedding
    esco_texts = [make_text(occ["title"], occ["description"]) for occ in esco_occs]
    onet_code_list = list(valid_onet.keys())
    onet_texts = [
        make_text(onet_descriptions[code]["title"], onet_descriptions[code]["description"])
        for code in onet_code_list
    ]

    # Embed all texts in one batch
    all_texts = esco_texts + onet_texts
    embeddings = embed_texts(client, all_texts)

    esco_embeds = embeddings[:len(esco_texts)]
    onet_embeds = embeddings[len(esco_texts):]

    # Compute similarity matrix: (num_esco, num_onet)
    sim_matrix = cosine_similarity(esco_embeds, onet_embeds)

    results = []
    for i, occ in enumerate(esco_occs):
        # Find O*NET codes above threshold
        scores = sim_matrix[i]
        matched_indices = np.where(scores >= threshold)[0]

        if len(matched_indices) == 0:
            # Take the single best match if nothing passes threshold
            best_idx = int(np.argmax(scores))
            best_score = scores[best_idx]
            # Only include if score is at least somewhat relevant
            if best_score >= threshold * 0.7:
                matched_indices = [best_idx]
            else:
                results.append({
                    "esco_uri": occ["uri"],
                    "isco_code": occ["isco_code"],
                    "esco_title": occ["title"],
                    "matched_onet_socs": "",
                    "alt_titles": "",
                    "match_scores": "",
                })
                continue

        # Collect matched O*NET-SOC codes and their alt titles
        matched_socs = []
        matched_scores = []
        all_alt_titles = set()
        for idx in matched_indices:
            code = onet_code_list[idx]
            matched_socs.append(code)
            matched_scores.append(f"{scores[idx]:.3f}")
            all_alt_titles.update(valid_onet[code])

        results.append({
            "esco_uri": occ["uri"],
            "isco_code": occ["isco_code"],
            "esco_title": occ["title"],
            "matched_onet_socs": "|".join(matched_socs),
            "alt_titles": "\n".join(sorted(all_alt_titles)),
            "match_scores": "|".join(matched_scores),
        })

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Semantically match ESCO occupations to O*NET-SOC codes"
    )
    parser.add_argument("--env", required=True, help="Path to .env file (e.g. .env.dev)")
    parser.add_argument("--threshold", type=float, default=0.45,
                        help="Cosine similarity threshold for matching (default: 0.45)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only process first 5 ISCO groups as a preview")
    parser.add_argument("--isco", type=str, default=None,
                        help="Process a single ISCO group (e.g. 5164) for testing")
    args = parser.parse_args()

    # Resolve env file
    env_path = Path(args.env)
    if not env_path.is_absolute():
        env_path = SKILLS_DIR / env_path
    if not env_path.exists():
        logger.error("Env file not found: %s", env_path)
        sys.exit(1)

    load_dotenv(env_path, override=True)

    # Verify required files exist
    for path, name in [(CROSSWALK_CSV, "Crosswalk CSV"), (ONET_OCCUPATION_DATA, "O*NET Occupation Data")]:
        if not path.exists():
            logger.error("%s not found: %s", name, path)
            sys.exit(1)

    logger.info("=" * 60)
    logger.info("ESCO → O*NET Semantic Matching")
    logger.info("  Threshold: %.2f", args.threshold)
    logger.info("  Dry run: %s", args.dry_run)
    logger.info("=" * 60)

    # 1. Load data
    logger.info("\n--- Loading data ---")
    onet_descriptions = load_onet_descriptions(ONET_OCCUPATION_DATA)
    crosswalk = load_crosswalk(CROSSWALK_CSV)

    conn = get_connection(env_path)
    esco_groups = load_esco_occupations(conn)
    conn.close()

    client = get_cohere_client()

    # 2. Match each ISCO group
    logger.info("\n--- Matching ISCO groups ---")
    all_results = []
    groups_to_process = sorted(esco_groups.keys())

    if args.isco:
        groups_to_process = [args.isco]
        if args.isco not in esco_groups:
            logger.error("ISCO group %s not found in ESCO data", args.isco)
            sys.exit(1)
    elif args.dry_run:
        groups_to_process = groups_to_process[:5]
        logger.info("[DRY RUN] Processing first 5 ISCO groups only")

    skipped = 0
    for i, isco in enumerate(groups_to_process):
        esco_occs = esco_groups[isco]

        if isco not in crosswalk:
            skipped += 1
            continue

        onet_codes = crosswalk[isco]
        logger.info(
            "[%d/%d] ISCO %s: %d ESCO occupations ↔ %d O*NET-SOC codes",
            i + 1, len(groups_to_process), isco, len(esco_occs), len(onet_codes),
        )

        results = match_isco_group(client, esco_occs, onet_codes, onet_descriptions, args.threshold)
        all_results.extend(results)

        # Print sample matches for visibility
        for r in results[:2]:
            matched = r["matched_onet_socs"] or "(none)"
            scores = r["match_scores"] or "n/a"
            n_titles = len(r["alt_titles"].split("\n")) if r["alt_titles"] else 0
            logger.info("    %s → %s (scores: %s, %d alt titles)",
                        r["esco_title"], matched, scores, n_titles)

    # 3. Write output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "esco_uri", "isco_code", "esco_title", "matched_onet_socs", "match_scores", "alt_titles"
        ])
        writer.writeheader()
        writer.writerows(all_results)

    logger.info("\n" + "=" * 60)
    logger.info("RESULTS")
    logger.info("=" * 60)
    logger.info("ISCO groups processed: %d", len(groups_to_process) - skipped)
    logger.info("ISCO groups skipped (no crosswalk): %d", skipped)
    logger.info("ESCO occupations matched: %d", len(all_results))
    matched_count = sum(1 for r in all_results if r["matched_onet_socs"])
    logger.info("  With O*NET match: %d", matched_count)
    logger.info("  Without match: %d", len(all_results) - matched_count)
    logger.info("Output: %s", OUTPUT_CSV)


if __name__ == "__main__":
    main()
