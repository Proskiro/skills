"""
Embed all skills that currently have NULL embeddings.

Run with:
    python -m embeddings.embed_skills
(from project root, assuming src is on PYTHONPATH or using `python -m`)
"""

from embeddings.embed_utils import (
    build_text_for_skill,
    embed_table,
)


def skill_text_builder(row: dict) -> str:
    """
    Map a DB row -> text to embed for skills.

    Assumes columns: preferred_title, description.
    Adjust if your schema differs.
    """
    preferred_title = row.get("preferred_title") or ""
    description = row.get("description")  # can be None
    return build_text_for_skill(preferred_title, description)


def main():
    embed_table(
        table_name="skills",
        id_column="uri",
        text_builder=skill_text_builder,
        # You can override model/version here if you like:
        # embedding_model="embed-english-v3.0",
        # embedding_version="cohere-embed-english-v3.0-2025-01",
    )


if __name__ == "__main__":
    main()
