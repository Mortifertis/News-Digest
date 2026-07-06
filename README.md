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
## Source management and fetch workflow

This POC now supports two source-management workflows on top of FastAPI and
SQLite only.

### Workflow A: manual source management

1. Seed every registry candidate without probing the network:

   ```bash
   python -m app.cli seed-all-candidates
   ```

2. Open `/sources` and enable or disable feeds manually. The page shows each
   feed's enabled state, metadata, latest fetch status, HTTP status, parsed
   entries, new/skipped article counts, last error, last fetch time, and last
   successful fetch time.

3. Use the per-feed **Test feed now** button to test a source immediately.
   A failed test does not disable the feed automatically because network
   failures can be temporary.

4. Use **Fetch enabled feeds now** on `/sources`, or run:

   ```bash
   python -m app.cli fetch
   ```

### Workflow B: automatic local probing

The existing accessible-source workflow remains available:

```bash
python -m app.cli seed-accessible-sources
```

This workflow is intended for local probing and can enable feeds that are
currently reachable from the machine running the POC.

### CLI commands

```bash
python -m app.cli seed-all-candidates
python -m app.cli seed-accessible-sources
python -m app.cli enable-feed "BBC World News"
python -m app.cli disable-feed "BBC World News"
python -m app.cli test-feed "BBC World News"
python -m app.cli fetch
python -m app.cli set-fetch-interval 180
python -m app.cli scheduler
```

Fetch output is summarized at the end. Example:

```text
Fetch run #15 finished: partial
Enabled feeds: 18
Successful: 7
Failed: 11
New articles: 143
Skipped existing: 62
Total entries parsed: 205

Failed feeds:
- BBC World: SSL handshake timed out
- Le Monde International: connection reset
```

### New routes

- `/sources` — source/feed management and fetch actions.
- `POST /sources/{feed_id}/enable` — enable a feed.
- `POST /sources/{feed_id}/disable` — disable a feed.
- `POST /sources/{feed_id}/test` — test one feed now.
- `POST /fetch/run` — fetch all enabled feeds now.
- `/fetch-runs` — recent fetch run history.
- `/fetch-runs/{id}` — fetch run details and per-feed results.
- `/settings` — configure the POC scheduler interval.

### Scheduler interval

Allowed `fetch_interval_minutes` values are:

- `0` — manual only
- `60` — every hour
- `180` — every 3 hours
- `360` — every 6 hours
- `720` — every 12 hours
- `1440` — every 24 hours

The scheduler is intentionally simple for the POC. It reads the setting, runs
fetch and clustering in a loop, prints summaries, sleeps for the configured
interval, and exits cleanly on `Ctrl+C`.

### Validation commands

```bash
python -m pytest -q
python -m ruff check .
python -m app.cli seed-all-candidates
python -m app.cli set-fetch-interval 180
python -m app.cli fetch
python -m app.cli cluster
python -m app.cli stats
uvicorn app.main:app --reload
```

Manual UI check:

- Open `/sources`.
- Enable a few feeds.
- Test feeds.
- Run **Fetch enabled feeds now**.
- Open the dashboard.
- Open `/fetch-runs`.
- Open `/feed` and try language/source/category filters.
- Open `/review`.
## Source catalog

The POC includes a structured English/French source-candidate catalog for
manual RSS source management. It covers public broadcasters, newspapers,
newswires, magazines, digital-native outlets, specialist publications, and
licensed/API-oriented candidates such as Reuters and AP. Candidate feeds are
not auto-enabled: operators should review `/sources`, filter by language,
country, category, URL status, outlet type, bias profile, and reliability
score, then enable only feeds they want to fetch.

Candidates without a confident official RSS URL are stored with an empty feed
URL and shown as "needs URL verification". They remain disabled and are skipped
by fetch runs until a verified URL is configured.

## Source metadata

Source-level metadata includes display name, primary/available languages,
country, region, homepage URL, outlet type, ownership type, paywall level,
default priority, tags, bias profile, rating confidence, rating notes, and an
editorial reliability heuristic. Feed-level metadata includes feed title, feed
URL, language, category, tags, official-URL status, URL confidence,
enabled-by-default, and notes.

The app stores RSS-provided article metadata only: titles, summaries,
canonical URLs, timestamps, and raw feed-entry payloads. Full article scraping
is intentionally not implemented.

## Editorial reliability heuristic

The `editorial_reliability_score` is an editorial reliability heuristic, not a
guarantee of truth and not an objective rating. The current rule of thumb is:

- `5`: major newswire, public broadcaster, or newspaper with strong editorial
  standards, corrections policy, broad original reporting, and low
  sensationalism.
- `4`: established major outlet with strong reporting but clearer editorial
  line, partial paywall, or more analysis/opinion mix.
- `3`: mainstream outlet with significant political/editorial framing,
  cable-news style, tabloid style, or click-driven tendencies, but still usable
  with caution.
- `2`: highly partisan, activist, sensationalist, or unreliable for a factual
  baseline.
- `1`: do not use by default; propaganda, conspiracy, or consistently
  unreliable.

Catalog scores are placeholders for POC triage. Verify ratings and source
policies before production use.

## Why VPN is not modeled

The application does not model VPN/no-VPN behavior. Source accessibility can
vary by user network, geography, rate limits, DNS, publisher policy, or
commercial access. Instead of guessing the cause, the POC tracks latest
per-feed probe/fetch status, HTTP status, parsed-entry counts, timestamps, and
error text.

## Fetch run workflow

Use `/sources` or `python -m app.cli fetch` to fetch enabled feeds. Web fetches
create a `FetchRun`, fetch enabled feeds with non-empty URLs, run clustering
when new articles are saved, and redirect to `/fetch-runs/{id}`. If there are
no enabled fetchable feeds, the app still records a `FetchRun` with status
`no_enabled_feeds` so the UI has an auditable run history.

`/fetch-runs` now has an empty state and a "Run fetch now" action. Fetch-run
detail pages summarize total feeds, successes, failures, entries, new/skipped
articles, cluster counts, and per-feed results with failed/error rows visible.

## Manual source management

The `/sources` page is the main control surface for source candidates. It shows
enabled status, source/feed names, language, country, category, tags, outlet
type, editorial reliability heuristic, bias profile, URL status, latest fetch
status, latest success timestamp, errors, and actions to enable, disable, test,
or fetch enabled feeds.

Commercial/licensed sources such as Reuters and AP may require separate API or
licensing arrangements. They are included only as disabled candidates unless an
official public feed/API is configured.

## Known limitations

- SQLite schema changes are handled by lightweight table creation and additive
  column checks; Alembic migrations are intentionally not added yet.
- There is no auth, Docker, PostgreSQL, Celery/RQ, Telegram integration, LLM
  processing, embeddings, or full article scraping.
- Source metadata and reliability scores are heuristic placeholders.
- RSS availability and publisher URL formats can change, so low-confidence or
  empty-URL candidates need manual verification.
- Fetching depends on the operator's current network and publisher access
  rules.
