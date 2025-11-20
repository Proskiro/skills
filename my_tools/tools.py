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
    
    # Extract ISCED-F and convert to hierarchical K code
    m_isced = re.search(r"/isced-f/(\d+)$", uri)
    if m_isced:
        isced_code = m_isced.group(1)
        return convert_isced_to_k(isced_code)

    # If not found, check if it’s a UUID-based URI
    if re.search(r"/[0-9a-fA-F-]{36}$", uri):
        return None  # This is a UUID, not a code

    # Otherwise, fallback: unrecognized format
    return None


def hash_suffix(uri: str, length: int = 3) -> str:
    """Returns a 3-digit deterministic suffix from the URI."""
    return str(int(hashlib.md5(uri.encode()).hexdigest(), 16) % 1000).zfill(length)


def convert_isced_to_k(code: str) -> str:
    """
    Converts an ISCED-F code into hierarchical K-code.
    Hierarchy:
        00   → K00
        001  → K00.1
        0011 → K00.1.1
        etc.
    """
    code = code.strip()

    # First two digits = root of the K hierarchy
    root = code[:2]

    if len(code) == 2:
        return f"K{root}"

    # Each additional digit is a deeper level
    sublevels = list(code[2:])
    return f"K{root}." + ".".join(sublevels)


def generate_skill_code(uri: str, broader_uris: list[str], code_lookup: dict) -> str:
    """
    Unified generator for S, K(ISCED-F), L, and UUID hierarchies.
    """

    # --- Step 0: direct coded root (S, K(from isced), L, T) ---
    root_code = extract_root_code(uri)
    if root_code:
        return root_code

    # --- Step 1: if no parents, this is a root-level UUID skill ---
    if not broader_uris:
        # Special rule: if URI belongs to K tree (root is /skill/K)
        if "/skill/K" in uri:
            return f"K{hash_suffix(uri)[:2]}"
        return f"U{hash_suffix(uri)[:2]}"

    # --- Step 2: Select the correct parent (PRECISION LOGIC) ---
    # Order already fixed earlier in your spider: coded parents first
    esco_parents = [u for u in broader_uris if extract_root_code(u)]
    uuid_parents = [u for u in broader_uris if not extract_root_code(u)]

    # Parent selection rule:
    if esco_parents:
        parent_uri = esco_parents[0]          # prefer coded parents
    else:
        parent_uri = uuid_parents[0]          # fallback to UUID parent

    # Try lookup first, otherwise extract code
    parent_code = code_lookup.get(parent_uri) or extract_root_code(parent_uri)

    # --- Step 3: If parent has no code, assign fallback ---
    if not parent_code:

        # SPECIAL CASE: K (Knowledge from ISCED-F or under K root)
        # If any ancestor or root is ISCED-F
        if any("/isced-f/" in u for u in broader_uris) or "/skill/K" in uri:
            parent_code = f"K{hash_suffix(parent_uri)[:2]}"
        else:
            # Generic UUID fallback
            parent_code = "U"

    # --- Step 4: generate child code ---
    return f"{parent_code}.{hash_suffix(uri)}"
