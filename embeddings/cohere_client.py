"""
Cohere client helper.

Usage:
    from embeddings.cohere_client import get_cohere_client, DEFAULT_MODEL, DEFAULT_INPUT_TYPE
"""

import os
from functools import lru_cache

import cohere
from dotenv import load_dotenv

load_dotenv()

# Default model + input_type for your use case
DEFAULT_MODEL = "embed-english-v3.0"  # later: "embed-multilingual-v3.0"
DEFAULT_INPUT_TYPE = "search_document"  # good for semantic search / retrieval


@lru_cache(maxsize=1)
def get_cohere_client() -> cohere.Client:
    """
    Lazily create and cache a single Cohere client instance.

    Requires COHERE_API_KEY to be set in the environment.
    """
    api_key = os.getenv("COHERE_API_KEY")
    if not api_key:
        raise RuntimeError("COHERE_API_KEY environment variable is not set")
    return cohere.Client(api_key)
