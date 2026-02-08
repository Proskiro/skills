# Skills Scraper

Web scraper and services for book/skill data collection.

## Setup

### Prerequisites

Install [uv](https://docs.astral.sh/uv/) (fast Python package manager):

```bash
# macOS
brew install uv

# Linux/WSL
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Install Dependencies

```bash
cd skills
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
uv pip install -r requirements-dev.txt
```

### Environment Variables

Create a `.env` file in the project root:

```env
# PostgreSQL Database
POSTGRES_HOST=your_host
POSTGRES_PORT=5432
POSTGRES_DB=your_database_name
POSTGRES_USER=your_username
POSTGRES_PASSWORD=your_password
SSL_CERT_PATH=path/to/global-bundle.pem

# Cohere API (for embeddings)
COHERE_API_KEY=your_cohere_api_key
```

## Running

```bash
# Activate venv
source .venv/bin/activate

# Run services
python -m my_services.search_books_for_skills --force-refresh --book-limit 60
```

## Development

```bash
# Run tests
pytest

# Lint & format
ruff check .
black .
```
