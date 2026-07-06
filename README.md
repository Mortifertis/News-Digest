# morti-news-digest

`morti-news-digest` is a self-hosted multilingual news digest prototype. It
fetches public RSS feeds, stores RSS-provided article metadata in SQLite,
normalizes text and URLs, detects exact and simple fuzzy duplicates, and shows a
basic FastAPI/Jinja2 dashboard for clustered news.

## Why this exists

The project proves the core idea for a future private news digest without adding
production infrastructure too early. This version intentionally avoids Telegram,
authentication, Docker, PostgreSQL, Celery, Redis, LLM summaries, embeddings,
semantic search, and full article scraping.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Initialize the database

```bash
python -m app.db.init_db
```

## Seed RSS feeds

```bash
python -m app.services.seed_sources
```

Seeded feeds include BBC World, BBC Technology, The Guardian World, The Guardian
Technology, Le Monde International, Le Monde Économie, France 24, and RFI. Some
additional France 24/RFI section feeds are left as TODOs until stable official
category URLs are verified.

## Fetch articles

```bash
python -m app.cli fetch
```

The fetcher logs broken feeds to the console and continues with the remaining
feeds.

## Run clustering

```bash
python -m app.cli cluster
```

## Start the web app

```bash
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/>.

## Main commands

A local run is typically:

```bash
python -m app.db.init_db
python -m app.services.seed_sources
python -m app.cli fetch
python -m app.cli cluster
uvicorn app.main:app --reload
```

## Clustering algorithm

Normalization lowercases text, trims and collapses whitespace, removes HTML from
summaries, removes repeated punctuation, strips URL fragments, removes common
tracking parameters, and hashes normalized title plus summary with SHA-256.

Clustering first checks exact duplicates by canonical URL or text hash. If no
exact duplicate exists, it compares normalized title plus summary against
clustered articles from the last 72 hours in the same language using RapidFuzz
`token_set_ratio`. Scores at or above 88 join an existing cluster; otherwise a
new cluster is created.

## Current limitations

- SQLite only; no PostgreSQL migrations yet.
- No background scheduler; fetch and cluster are manual CLI commands.
- No authentication or user-specific preferences.
- No full article scraping, paywall handling, translations, or summaries.
- RSS URL availability may change; failed feeds are reported at fetch time.

## Next steps

- Add Alembic migrations before changing the schema frequently.
- Add scheduled fetches and better operational logging.
- Add source/feed management forms in the web UI.