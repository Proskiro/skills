from typing import Dict

import numpy as np

from embeddings.cohere_client import DEFAULT_MODEL, get_cohere_client


def compute_similarity(skill: Dict, book: Dict) -> float:
    """Compute semantic similarity between a skill and a book."""

    co = get_cohere_client()  # Use your existing cached client

    skill_text = f"{skill['title']}: {skill.get('description', '')}"
    book_text = f"{book['title']}: {book.get('description', '')}"

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
