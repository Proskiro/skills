"""
Shared embedding utilities:
- DB connection helper
- batching helpers
- generic "embed and update" pipeline logic
"""

from typing import Callable, Iterable, List, Sequence, Tuple

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from embeddings.cohere_client import (
    DEFAULT_INPUT_TYPE,
    DEFAULT_MODEL,
    get_cohere_client,
)

load_dotenv()


# --- Types ---

Row = Tuple[int, str]  # (id, text_to_embed)

# --- Batching helpers ---


def chunked(iterable: Iterable, size: int) -> Iterable[List]:
    """
    Yield lists of up to `size` items from any iterable.
    """
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


# --- Core embedding logic ---


def embed_text_batch(
    texts: Sequence[str],
    model: str = DEFAULT_MODEL,
    input_type: str = DEFAULT_INPUT_TYPE,
) -> List[List[float]]:
    """
    Call Cohere embed API for a batch of texts and return embeddings.
    """
    if not texts:
        return []

    client = get_cohere_client()
    resp = client.embed(
        model=model,
        texts=list(texts),
        input_type=input_type,
    )
    # Cohere returns resp.embeddings as a list of float vectors
    return resp.embeddings


def build_text_for_skill(preferred_title: str, description: str | None) -> str:
    """
    Construct the text that will be embedded for skills.
    Adjust if you want a different format.
    """
    description = description or ""
    text = f"{preferred_title}. {description}".strip()
    return text


def build_text_for_book(title: str, description: str | None) -> str:
    """
    Construct the text that will be embedded for books.
    """
    description = description or ""
    text = f"{title}. {description}".strip()
    return text


def fetch_rows_without_embeddings(
    conn,
    table_name: str,
    id_column: str,
    text_builder: Callable[[dict], str],
    batch_size: int = 500,
):
    """
    Generator that yields (id, text_to_embed) for rows whose embedding is NULL.

    `text_builder` receives a dict(row) and must return the string to embed.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        offset = 0
        while True:
            cur.execute(
                f"""
                SELECT *
                FROM {table_name}
                WHERE embedding IS NULL
                ORDER BY {id_column}
                LIMIT %s OFFSET %s
                """,
                (batch_size, offset),
            )
            rows = cur.fetchall()
            if not rows:
                break

            for row in rows:
                text = text_builder(row)
                if text:
                    yield (row[id_column], text)

            offset += batch_size


def update_embeddings_in_db(
    conn,
    table_name: str,
    id_column: str,
    id_and_embeddings: Sequence[Tuple[int, List[float]]],
    embedding_model: str,
    embedding_version: str,
):
    """
    Bulk update embeddings for a given table.

    Expects each element in id_and_embeddings as (id, embedding_vector).
    """
    if not id_and_embeddings:
        return

    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(
            cur,
            f"""
            UPDATE {table_name}
            SET
                embedding = %s,
                embedding_model = %s,
                embedding_version = %s
            WHERE {id_column} = %s
            """,
            [
                (emb, embedding_model, embedding_version, row_id)
                for row_id, emb in id_and_embeddings
            ],
            page_size=100,
        )
    conn.commit()


def embed_table(
    table_name: str,
    id_column: str,
    text_builder: Callable[[dict], str],
    embedding_model: str = DEFAULT_MODEL,
    embedding_version: str = "cohere-embed-english-v3.0-2025-01",
    batch_fetch_size: int = 500,
    batch_embed_size: int = 64,
):
    """
    High-level function:
    - fetch rows with NULL embedding
    - embed in batches
    - write embeddings back to DB

    `text_builder` receives a row dict and must return the string to embed.
    """
    conn = get_db_connection()
    try:
        print(
            f"Starting embedding for table '{table_name}' "
            f"with model={embedding_model}, version={embedding_version}"
        )

        row_generator = fetch_rows_without_embeddings(
            conn,
            table_name=table_name,
            id_column=id_column,
            text_builder=text_builder,
            batch_size=batch_fetch_size,
        )

        total_processed = 0
        for rows_batch in chunked(row_generator, batch_embed_size):
            ids = [row_id for (row_id, _) in rows_batch]
            texts = [text for (_, text) in rows_batch]

            embeddings = embed_text_batch(texts, model=embedding_model)

            id_and_embeddings = list(zip(ids, embeddings))
            update_embeddings_in_db(
                conn,
                table_name=table_name,
                id_column=id_column,
                id_and_embeddings=id_and_embeddings,
                embedding_model=embedding_model,
                embedding_version=embedding_version,
            )

            total_processed += len(ids)
            print(f"Processed {total_processed} rows for {table_name}...")

        print(f"Done. Total rows processed for {table_name}: {total_processed}")
    finally:
        conn.close()
