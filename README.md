# Skills Data Pipeline

A multi-source book recommendation pipeline that maps learning resources to professional skills using semantic AI. Built as the data backend for a career development platform covering thousands of ESCO-standard occupations.

## What it does

For each knowledge skill in the database, the pipeline:

1. Queries **Google Books** and **Open Library** across multiple query variants
2. Filters candidates against hard quality gates (publication year, ISBN, language, fiction detection, spam title detection)
3. **Semantically re-ranks** results using Cohere's rerank API — comparing each book against the skill's description to surface genuinely relevant material over keyword matches
4. Scores and ranks final candidates using a weighted model (publisher reputation, recency, rating, source reliability)
5. Persists the top results to PostgreSQL and generates affiliate links

The pipeline handles ~3,000+ knowledge skills across thousands of occupation–skill pairs and runs incrementally, skipping recently-processed skills and applying cascading fallback strategies when coverage is thin.

## Technical highlights

- **Multi-source deduplication** — results from both APIs are merged and deduplicated before ranking, with description enrichment for Open Library books that lack metadata
- **Semantic filtering with Cohere** — both the rerank API (primary) and cosine similarity over embeddings (legacy) are supported, with the model selectable at runtime
- **ESCO ↔ O*NET taxonomy mapping** — a separate script uses Cohere embeddings to semantically align European (ESCO) occupation codes with US (O*NET-SOC) codes, enabling cross-standard job title enrichment
- **API key rotation** — Google Books supports multiple keys with automatic rotation on quota exhaustion
- **Containerised** — runs in a Podman/Docker container with the AWS RDS SSL bundle baked in at build time

## Stack

| Layer | Technology |
|---|---|
| Scraping | Scrapy + Zyte proxy |
| Book APIs | Google Books API, Open Library API |
| Semantic AI | Cohere (rerank + embeddings) |
| Database | PostgreSQL on AWS RDS (SSL) |
| Data scripts | Python + psycopg2 + pandas |
| Packaging | uv |
| Container | Podman (Containerfile) |

## Setup

```bash
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
```

Copy `.env.dev` to `.env` and fill in your credentials (PostgreSQL connection, Cohere API key, Google Books API key, Zyte API key).

## Running

```bash
# Run the pipeline
python -m my_services.search_books_for_skills

# Check coverage progress
python check_progress.py

# Crawl ESCO occupations
scrapy crawl esco_occupations -a freshness_days=30
```

## Development

```bash
pytest
ruff check .
black .
```
