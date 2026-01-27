from typing import Dict

import numpy as np
from sentence_transformers import SentenceTransformer

# Load model once at module level (lazy loading would be better for large apps)
model = SentenceTransformer("all-MiniLM-L6-v2")


def compute_similarity(skill: Dict, book: Dict) -> float:
    """Semantic filtering utilities for books and skills."""

    skill_text = f"{skill['title']} : {skill.get('description', '')}"
    book_text = f"{book['title']} : {book.get('description', '')}"

    embeddings = model.encode([skill_text, book_text])

    # Cosine similarity
    similarity = np.dot(embeddings[0], embeddings[1]) / (
        np.linalg.norm(embeddings[0]) * np.linalg.norm(embeddings[1])
    )

    return float(similarity)
