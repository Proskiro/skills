import time
from typing import Dict, List, Tuple

import numpy as np

from embeddings.cohere_client import DEFAULT_MODEL, get_cohere_client

RERANK_MAX_RETRIES = 3
RERANK_BASE_DELAY = 5  # seconds


def compute_similarity(skill: Dict, book: Dict) -> float:
    """Compute semantic similarity between a skill and a book."""

    co = get_cohere_client()  # Use your existing cached client

    skill_text = f"{skill['title']}: {skill['description']}"
    book_text = f"{book.get('title', '')}: {book.get('description', '')}"

    # Embed skill (as query)
    skill_response = co.embed(
        texts=[skill_text],
        model=DEFAULT_MODEL,
        input_type="search_query",
    )

    # Embed book (as document)
    book_response = co.embed(
        texts=[book_text],
        model=DEFAULT_MODEL,
        input_type="search_document",
    )

    # Extract embeddings
    skill_embedding = np.array(skill_response.embeddings[0])
    book_embedding = np.array(book_response.embeddings[0])

    # Cosine similarity
    similarity = np.dot(skill_embedding, book_embedding) / (
        np.linalg.norm(skill_embedding) * np.linalg.norm(book_embedding)
    )

    return float(similarity)


def rerank_books_for_skill(
    skill: Dict,
    books: List[Dict],
    top_n: int = 5,
) -> List[Tuple[Dict, float]]:
    """
    Rerank books for a professional skill using Cohere's rerank API.

    This approach is better than embedding similarity because:
    - It allows rich domain context in the query
    - Single API call for all books
    - Model is specifically trained for relevance ranking

    Args:
        skill: Skill dict with 'title' and optionally 'description'
        books: List of book dicts with 'title' and optionally 'description'
        profession_title: Optional profession context (e.g., "Data Scientist")
        top_n: Number of top results to return

    Returns:
        List of (book, relevance_score) tuples, sorted by relevance
    """
    if not books:
        return []

    co = get_cohere_client()

    if occupation_title := skill.get("occupation_title"):
        query = (
            f"A {occupation_title} needs to learn: {skill['title']}. "
            f"{skill['description']} "
            f"Find practical books for professional development and self-improvement. "
            f"Not fiction, not children's books, not academic theory textbooks."
        )
    else:
        query = (
            f"Professional skill to develop: {skill['title']}. "
            f"{skill['description']} "
            f"Find practical books for workplace learning and career growth. "
            f"Not fiction, not children's books, not purely academic."
        )

    documents = [f"{b.get('title', '')}: {b.get('description', '')}" for b in books]

    last_error = None
    for attempt in range(1, RERANK_MAX_RETRIES + 1):
        try:
            response = co.rerank(
                query=query,
                documents=documents,
                model="rerank-english-v3.0",
                top_n=top_n,
            )
            return [(books[r.index], r.relevance_score) for r in response.results]
        except Exception as e:
            last_error = e
            if attempt < RERANK_MAX_RETRIES:
                delay = RERANK_BASE_DELAY * attempt
                print(f"  [RERANK RETRY] Attempt {attempt}/{RERANK_MAX_RETRIES} failed: {e}. Retrying in {delay}s...")
                time.sleep(delay)
            else:
                print(f"  [RERANK FAILED] All {RERANK_MAX_RETRIES} attempts failed: {e}")

    raise last_error
