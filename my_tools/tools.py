import hashlib
import re


def extract_root_code(uri: str) -> str | None:
    """
    Extracts the root code (like S3.2.5 or C2131) from a given ESCO URI.
    Returns None if the URI doesn't follow that format (e.g., UUID-based).
    """
    if not uri:
        return None

    # Match either a Skill or Occupation code (e.g. S3.2.5 or C2131)
    match = re.search(r"/skill/([SKLT][\w.\-]+)$", uri)
    if match:
        return match.group(1)

    # If not found, check if it’s a UUID-based URI
    if re.search(r"/[0-9a-fA-F-]{36}$", uri):
        return None  # This is a UUID, not a code

    # Otherwise, fallback: unrecognized format
    return None


def hash_suffix(uri: str, length: int = 3) -> str:
    """Returns a 3-digit deterministic suffix from the URI."""
    return str(int(hashlib.md5(uri.encode()).hexdigest(), 16) % 1000).zfill(length)


def generate_skill_code(uri: str, broader_uris: list[str], code_lookup: dict) -> str:
    """
    Generates a stable hierarchical ESCO-like skill code.
    - Keeps real S/K/L/T codes if available.
    - For children: extends nearest coded parent's code (e.g. S3.5.123).
    - For orphans or UUID ancestry: assigns Uxx fallback.
    """

    root_code = extract_root_code(uri)
    if not broader_uris:
        # Root-level skill (like S3.5)
        return root_code or f"U{hash_suffix(uri)[:2]}"

    # --- Step 1: find parent code ---
    parent_code = None
    for parent_uri in broader_uris:
        # Prefer already-known parent code
        parent_code = code_lookup.get(parent_uri)
        if parent_code:
            break

        # Otherwise, try extracting a real ESCO-style root code (S/K/L/T...)
        candidate = extract_root_code(parent_uri)
        if candidate:
            parent_code = candidate
            break

    # --- Step 2: fallback if parent is UUID-style or missing ---
    if not parent_code:
        # Detect UUID pattern to make fallback explicit
        if re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", parent_uri):
            parent_code = "U"
        else:
            parent_code = "U"

    # --- Step 3: keep real ESCO codes as-is ---
    if root_code:
        return root_code

    # --- Step 4: extend parent's code deterministically ---
    return f"{parent_code}.{hash_suffix(uri)}"
