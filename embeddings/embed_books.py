"""
Embed all books that currently have NULL embeddings.

Run with:
    python -m embeddings.embed_books
"""

from embeddings.embed_utils import (
    build_text_for_book,
    embed_table,
)


def book_text_builder(row: dict) -> str:
    """
    Map a DB row -> text to embed for books.

    Assumes columns: title, description.
    If your books table uses a different field for description
    (e.g. 'summary' or 'subtitle'), adjust here.
    """
    title = row.get("title") or ""
    description = row.get("description")  # can be None
    return build_text_for_book(title, description)


def main():
    embed_table(
        table_name="books",
        id_column="id",
        text_builder=book_text_builder,
    )


if __name__ == "__main__":
    main()
