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
    root_code = extract_root_code(uri)
    if not broader_uris:
        return root_code or f"U{hash_suffix(uri)[:2]}"

    # --- Always prioritise coded parents ---
    esco_parents = [u for u in broader_uris if extract_root_code(u)]
    uuid_parents = [u for u in broader_uris if not extract_root_code(u)]

    if esco_parents:
        parent_uri = esco_parents[0]
    else:
        parent_uri = uuid_parents[0]

    parent_code = code_lookup.get(parent_uri) or extract_root_code(parent_uri)

    # --- Fallback for UUID ancestry ---
    if not parent_code:
        parent_code = "U"

    # --- Keep real ESCO codes as-is ---
    if root_code:
        return root_code

    # --- Extend parent code deterministically ---
    return f"{parent_code}.{hash_suffix(uri)}"
