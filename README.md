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
## POC validation

This prototype is intentionally limited to FastAPI, SQLite, local CLI commands,
and RSS metadata. It does not use Docker, PostgreSQL, Alembic, authentication,
background workers, LLM summaries, or full article scraping.

### Prepare or update the SQLite schema

Run this after pulling changes. It creates the tables and safely adds the POC
fetch-status columns to an existing SQLite database when they are missing:

```bash
python -m app.db.init_db
python -m app.services.seed_sources
```

If the local POC database is disposable, the simplest full reset is to stop the
app, delete `morti_news_digest.db`, and run the two commands above again. To keep
configured sources and feeds while clearing imported story data, use:

```bash
python -m app.cli reset-data
```

### Live RSS test

Run a live fetch, cluster the imported articles, and inspect statistics:

```bash
python -m app.cli fetch
python -m app.cli cluster
python -m app.cli stats
```

`fetch` prints one diagnostic block per feed: source, feed title, URL, HTTP
status when available, parsed entries, saved articles, skipped articles, final
status, and error text for failed feeds. A broken feed must be visible in this
output and must not stop later feeds from being fetched.

Expected shape of live output:

```text
------------------------------------------------------------------------
Source: The Guardian
Feed: The Guardian World
URL: https://www.theguardian.com/world/rss
HTTP status: 200
Parsed entries: 50
New articles saved: 50
Skipped existing articles: 0
Status: success
Saved 50 new articles.
```

Failed feeds should look similar but with `Status: failed` and an `Error:` line.

### Offline demo clustering test

Use the deterministic local fixture when network/VPN conditions make live RSS
unreliable:

```bash
python -m app.cli load-demo
```

By default this clears article and cluster tables, keeps sources and feeds, loads
`fixtures/demo_articles.json`, and runs clustering. Use `--no-reset` only when
you intentionally want to append missing demo articles to existing data.

Expected demo stats should show 12 articles, fewer clusters than articles, at
least two multi-article clusters, and unrelated demo stories remaining as
singletons:

```text
Loaded 12 demo articles.
Total articles: 12
Total clusters: 8
Multi-article clusters count: 2
```

### Refetch shortcut

After validating feed setup, run the whole live loop with:

```bash
python -m app.cli refetch
```

This runs `fetch`, `cluster`, and `stats` in sequence.

### Manual review page

Start the app and open `/review`:

```bash
uvicorn app.main:app --reload
```

The review page orders clusters by article count descending and shows the cluster
title, language, article count, unique source count, article titles, source
names, match type, similarity score, and original URLs. Use it to inspect false
positives and false negatives. The top navigation includes a Review link.

The `/feed` page remains the lightweight digest view. Its cards now show a
language badge, article count, unique source count, source names, first/last seen
timestamps, the original lead-article link, and a cluster-details link.

### Interpreting stats

`python -m app.cli stats` prints totals for sources, feeds, enabled feeds,
successful feeds, failed feeds, articles, and clusters. It also breaks down
articles by source and language, clusters by language, singleton clusters,
multi-article clusters, multi-source clusters, average articles per cluster, and
the top 10 largest clusters.

Useful signals:

- `successful feeds` and `failed feeds` reveal source availability.
- `articles per source` helps detect one source dominating the dataset.
- `singleton clusters count` versus `multi-article clusters count` shows whether
  clustering is combining related stories.
- `multi-source clusters count` shows whether the digest is finding cross-source
  coverage.
- `top 10 largest clusters` should be reviewed for false positives.

### Go/no-go criteria

GO if:

- live fetch works for at least some feeds;
- broken feeds do not crash the app;
- demo clustering produces multi-article clusters;
- `/review` makes false positives easy to inspect;
- repeated fetch and cluster commands are idempotent.

NO-GO or redesign if:

- repeated fetch creates duplicates;
- repeated clustering creates duplicate links;
- fuzzy matching creates many false positives;
- source failures are not visible to the user.