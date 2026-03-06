"""
Semantic matching: ESCO occupations → O*NET-SOC codes within each ISCO group.

Uses Cohere embeddings to match each ESCO occupation (title + description)
to the most relevant O*NET-SOC code(s) within its ISCO group. Then filters
the alternate titles from matched O*NET codes by per-title semantic relevance.

Improvements over naive matching:
  1. Higher default threshold (0.55) to reduce false O*NET code matches
  2. Cross-domain blacklist — blocks O*NET major groups that are semantically
     adjacent but functionally unrelated (e.g. transportation ↔ animal care)
  3. Top-N cap — keeps at most 3 best O*NET code matches per ESCO occupation
  4. Second-stage title filter — after code matching, each alt title is
     embedded and compared to the ESCO occupation; only titles above a
     title-level threshold are kept

Usage:
    python scripts/match_onet_to_esco.py --env .env.dev --dry-run
    python scripts/match_onet_to_esco.py --env .env.dev --threshold 0.55
    python scripts/match_onet_to_esco.py --env .env.dev --isco 5164
    python scripts/match_onet_to_esco.py --env .env.dev

Output:
    scripts/output/esco_onet_matched_titles.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import cohere
import httpx
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
CHECKPOINT_FILE = OUTPUT_DIR / "match_checkpoint.json"

# Retry settings
MAX_RETRIES = 3
RETRY_BASE_DELAY = 5  # seconds

# Cohere settings
EMBED_MODEL = "embed-english-v3.0"
EMBED_INPUT_TYPE = "search_document"
EMBED_BATCH_SIZE = 96  # Cohere's max batch size

# Matching defaults
DEFAULT_CODE_THRESHOLD = 0.55    # Min similarity for O*NET code match
DEFAULT_TITLE_THRESHOLD = 0.48   # Min similarity for keeping an alt title
DEFAULT_MAX_ONET_CODES = 3       # Max O*NET codes to match per ESCO occupation

# ---------------------------------------------------------------------------
# Cross-domain blacklist
# ---------------------------------------------------------------------------
# Maps ISCO 2-digit major groups to O*NET SOC 2-digit prefixes that should
# never match, even if embeddings score above threshold.  These are codes
# that share superficially similar words ("attendant", "supervisor") but
# belong to completely unrelated domains.
#
# ISCO major groups:  https://www.ilo.org/public/english/bureau/stat/isco/isco08/
# SOC major groups:   https://www.bls.gov/soc/2018/major_groups.htm

CROSS_DOMAIN_BLACKLIST: dict[str, set[str]] = {
    # ISCO 5 – Service & Sales → block Transportation (53)
    "51": {"53"},
    "52": {"53"},
    "53": {"53"},
    "54": {"53"},
    # ISCO 6 – Agriculture → block Transportation (53)
    "61": {"53"},
    "62": {"53"},
    "63": {"53"},
    # ISCO 9 – Elementary occupations → block Military (55)
    "91": {"55"},
    "92": {"55"},
    "93": {"55"},
    "94": {"55"},
    "95": {"55"},
    "96": {"55"},
}


def is_blacklisted(isco_4digit: str, onet_code: str) -> bool:
    """Check if an O*NET code is blacklisted for a given ISCO group."""
    isco_2 = isco_4digit[:2]
    onet_2 = onet_code[:2]
    blocked = CROSS_DOMAIN_BLACKLIST.get(isco_2, set())
    return onet_2 in blocked


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
    Embed a list of texts using Cohere, handling batching with retries.

    Returns: numpy array of shape (len(texts), embedding_dim)
    """
    all_embeddings = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i:i + EMBED_BATCH_SIZE]
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = client.embed(
                    texts=batch,
                    model=EMBED_MODEL,
                    input_type=EMBED_INPUT_TYPE,
                )
                all_embeddings.extend(response.embeddings)
                break
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as e:
                if attempt == MAX_RETRIES:
                    raise
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning("    Embed request failed (attempt %d/%d): %s. Retrying in %ds...",
                               attempt, MAX_RETRIES, e, delay)
                time.sleep(delay)
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


def filter_alt_titles(
    client: cohere.Client,
    esco_embed: np.ndarray,
    alt_titles: list[str],
    title_threshold: float,
) -> list[str]:
    """
    Second-stage filter: keep only alt titles semantically close to the
    ESCO occupation.

    Args:
        esco_embed: (1, d) embedding of the ESCO occupation
        alt_titles: candidate alternate titles
        title_threshold: minimum cosine similarity to keep a title

    Returns: filtered list of alt titles, sorted alphabetically
    """
    if not alt_titles:
        return []

    title_embeds = embed_texts(client, alt_titles)
    sims = cosine_similarity(esco_embed.reshape(1, -1), title_embeds)[0]
    kept = [t for t, s in zip(alt_titles, sims) if s >= title_threshold]
    return sorted(kept)


def match_isco_group(
    client: cohere.Client,
    esco_occs: list[dict],
    onet_codes: dict[str, list[str]],
    onet_descriptions: dict[str, dict],
    isco_code: str,
    threshold: float = DEFAULT_CODE_THRESHOLD,
    title_threshold: float = DEFAULT_TITLE_THRESHOLD,
    max_onet_codes: int = DEFAULT_MAX_ONET_CODES,
) -> list[dict]:
    """
    Match ESCO occupations to O*NET-SOC codes within a single ISCO group.

    Steps:
      1. Blacklist-filter O*NET codes that are cross-domain
      2. Embed ESCO occupations + O*NET occupation descriptions
      3. For each ESCO occupation, pick top-N O*NET codes above threshold
      4. Collect candidate alt titles from matched codes
      5. Second-stage: embed alt titles, keep only those above title_threshold

    Returns list of {esco_uri, isco_code, matched_onet_socs, alt_titles, ...}
    """
    # Filter O*NET codes: must have descriptions & not be blacklisted
    valid_onet = {}
    blacklisted_count = 0
    for code, alt_titles in onet_codes.items():
        if code not in onet_descriptions:
            continue
        if is_blacklisted(isco_code, code):
            blacklisted_count += 1
            continue
        valid_onet[code] = alt_titles

    if blacklisted_count:
        logger.info("    Blacklisted %d cross-domain O*NET codes", blacklisted_count)

    if not valid_onet:
        return []

    # ── Stage 1: match ESCO → O*NET codes ──
    esco_texts = [make_text(occ["title"], occ["description"]) for occ in esco_occs]
    onet_code_list = list(valid_onet.keys())
    onet_texts = [
        make_text(onet_descriptions[code]["title"], onet_descriptions[code]["description"])
        for code in onet_code_list
    ]

    all_texts = esco_texts + onet_texts
    embeddings = embed_texts(client, all_texts)

    esco_embeds = embeddings[:len(esco_texts)]
    onet_embeds = embeddings[len(esco_texts):]

    sim_matrix = cosine_similarity(esco_embeds, onet_embeds)

    results = []
    for i, occ in enumerate(esco_occs):
        scores = sim_matrix[i]

        # Indices above threshold, sorted descending, capped to top-N
        above = np.where(scores >= threshold)[0]
        if len(above) == 0:
            # Fallback: single best if reasonably close
            best_idx = int(np.argmax(scores))
            if scores[best_idx] >= threshold * 0.9:
                above = np.array([best_idx])
            else:
                results.append({
                    "esco_uri": occ["uri"],
                    "isco_code": occ["isco_code"],
                    "esco_title": occ["title"],
                    "matched_onet_socs": "",
                    "alt_titles": "",
                    "match_scores": "",
                    "titles_before_filter": 0,
                    "titles_after_filter": 0,
                })
                continue

        # Sort by score descending, keep top-N
        ranked = sorted(above, key=lambda idx: scores[idx], reverse=True)
        top_indices = ranked[:max_onet_codes]

        # Gather matched codes and all candidate alt titles
        matched_socs = []
        matched_scores = []
        candidate_titles: set[str] = set()
        for idx in top_indices:
            code = onet_code_list[idx]
            matched_socs.append(code)
            matched_scores.append(f"{scores[idx]:.3f}")
            candidate_titles.update(valid_onet[code])

        # ── Stage 2: filter alt titles by per-title similarity ──
        candidate_list = sorted(candidate_titles)
        filtered_titles = filter_alt_titles(
            client, esco_embeds[i], candidate_list, title_threshold
        )

        results.append({
            "esco_uri": occ["uri"],
            "isco_code": occ["isco_code"],
            "esco_title": occ["title"],
            "matched_onet_socs": "|".join(matched_socs),
            "alt_titles": "\n".join(filtered_titles),
            "match_scores": "|".join(matched_scores),
            "titles_before_filter": len(candidate_list),
            "titles_after_filter": len(filtered_titles),
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
    parser.add_argument("--threshold", type=float, default=DEFAULT_CODE_THRESHOLD,
                        help=f"Cosine similarity threshold for O*NET code matching (default: {DEFAULT_CODE_THRESHOLD})")
    parser.add_argument("--title-threshold", type=float, default=DEFAULT_TITLE_THRESHOLD,
                        help=f"Cosine similarity threshold for alt title filtering (default: {DEFAULT_TITLE_THRESHOLD})")
    parser.add_argument("--max-codes", type=int, default=DEFAULT_MAX_ONET_CODES,
                        help=f"Max O*NET codes to match per ESCO occupation (default: {DEFAULT_MAX_ONET_CODES})")
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
    logger.info("  Code threshold: %.2f", args.threshold)
    logger.info("  Title threshold: %.2f", args.title_threshold)
    logger.info("  Max O*NET codes: %d", args.max_codes)
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

    # Load checkpoint if resuming
    completed_iscos: set[str] = set()
    if not args.isco and CHECKPOINT_FILE.exists():
        checkpoint = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        completed_iscos = set(checkpoint.get("completed_iscos", []))
        all_results = checkpoint.get("results", [])
        logger.info("Resuming from checkpoint: %d ISCO groups already done, %d results loaded",
                    len(completed_iscos), len(all_results))

    skipped = 0
    for i, isco in enumerate(groups_to_process):
        if isco in completed_iscos:
            continue

        esco_occs = esco_groups[isco]

        if isco not in crosswalk:
            skipped += 1
            completed_iscos.add(isco)
            continue

        onet_codes = crosswalk[isco]
        logger.info(
            "[%d/%d] ISCO %s: %d ESCO occupations ↔ %d O*NET-SOC codes",
            i + 1, len(groups_to_process), isco, len(esco_occs), len(onet_codes),
        )

        results = match_isco_group(
            client, esco_occs, onet_codes, onet_descriptions,
            isco_code=isco,
            threshold=args.threshold,
            title_threshold=args.title_threshold,
            max_onet_codes=args.max_codes,
        )
        all_results.extend(results)
        completed_iscos.add(isco)

        # Save checkpoint after each ISCO group
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        CHECKPOINT_FILE.write_text(json.dumps({
            "completed_iscos": sorted(completed_iscos),
            "results": all_results,
        }, ensure_ascii=False), encoding="utf-8")

        # Print sample matches for visibility
        for r in results[:2]:
            matched = r["matched_onet_socs"] or "(none)"
            scores = r["match_scores"] or "n/a"
            before = r.get("titles_before_filter", 0)
            after = r.get("titles_after_filter", 0)
            logger.info("    %s → %s (scores: %s, titles: %d→%d)",
                        r["esco_title"], matched, scores, before, after)

    # 3. Write output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Strip internal tracking fields before writing
    output_fields = [
        "esco_uri", "isco_code", "esco_title", "matched_onet_socs",
        "match_scores", "titles_before_filter", "titles_after_filter", "alt_titles",
    ]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_fields)
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
    total_before = sum(r.get("titles_before_filter", 0) for r in all_results)
    total_after = sum(r.get("titles_after_filter", 0) for r in all_results)
    if total_before:
        logger.info("  Alt titles: %d → %d (%.0f%% filtered out)",
                    total_before, total_after,
                    (1 - total_after / total_before) * 100)
    logger.info("Output: %s", OUTPUT_CSV)

    # Clean up checkpoint on successful completion
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        logger.info("Checkpoint file removed")


if __name__ == "__main__":
    main()
