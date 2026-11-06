# University Crawl Index

A small CLI that turns lists of target universities into:

1. A **canonical, versioned JSON file** (`dist/universities.canonical.json`) — validated,
   deduped, deterministically sorted. Committed to git; the stable source of truth every
   crawl run starts from.
2. A **SQL "master index"** (SQLite, one file: `uni_index.db`) that dedupes universities
   by domain and tracks crawl status over time.

## How it fits together

```
data/sources/imported/<cc>.yaml   <- generated from the Hipo dataset (uni-index import-hipo)
                                     never hand-edited; regenerate instead
data/sources/curated/<cc>.yaml    <- you edit these: override imported entries field by
                                     field (matched by domain), or add new ones
        │
        │  uni-index normalize   (imported first, curated second: the later layer wins)
        ▼
dist/universities.canonical.json  <- generated: validated, deduped, sorted — commit this
        │        (CI fails the build if it's out of date)
        │
        │  uni-index sync-db
        ▼
uni_index.db (SQLite)  ->  table "universities": domain (unique), country, type,
                            subdomains, priority, crawl_status, last_crawled_at, ...
        ▲
        │  uni-index mark <domain> --status done|failed|...
        │
   your crawler (a separate program — reads 'pending' rows, crawls them, reports back)
```

One file per 2-letter country code (`us.yaml`, `gb.yaml`, `in.yaml`, ...): small files mean
collaborators rarely touch the same lines, and each file can be given its own reviewers.
`data/reports/hipo_import.yaml` lists every entry the importer skipped, and why.

## 1. One-time setup

```bash
git clone <your-repo-url>
cd <your-repo-folder>
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"     # the package, plus pytest
```

Run commands through `.venv/bin/...` (or activate the venv first). A venv records absolute
paths: if you rename or move the project folder, rebuild the venv.

## 2. Day-to-day usage

```bash
uni-index normalize   # merge imported + curated -> dist/universities.canonical.json
uni-index sync-db     # load the canonical file into the SQL master index
uni-index status      # crawl-status summary
```

Commit your `data/sources/curated/` edit **and** the regenerated canonical file together.

## 3. Adding or changing a university

Edit `data/sources/curated/<cc>.yaml` (create it if that country has no file yet — copy the
header from an existing curated file).

Override an imported entry: its exact domain, plus only the fields you want changed.

```yaml
universities:
  - domain: mit.edu
    type: private
    priority: 1
    subdomains: [admissions.mit.edu, catalog.mit.edu]
```

Add a new one: every required field.

```yaml
  - domain: example.edu
    country: US        # 2-letter code (required)
    type: public       # public | private | community | research | other  (default: other)
    priority: 2        # 1 (crawl first) .. 5 (crawl last)                   (default: 3)
    subdomains: []
```

Empty values never override. `normalize` reports how many overrides and new entries the
curated layer applied: a "new entry" where you meant an override means the domain doesn't
match the imported one exactly.

## 4. Refreshing the imported layer

```bash
curl -f -o world_universities.json https://raw.githubusercontent.com/Hipo/university-domains-list/master/world_universities_and_domains.json
uni-index import-hipo world_universities.json
```

This regenerates `data/sources/imported/` from scratch and rewrites the report. Curated edits
are untouched. Read the report, run `normalize`, commit.

## 5. Reporting crawl results back (for whatever crawls the list)

```bash
uni-index mark mit.edu --status in_progress
uni-index mark mit.edu --status done
uni-index mark mit.edu --status failed --error "connection timeout"
```

Re-running `sync-db` later **won't** reset a domain's status back to `pending` unless
that domain's canonical record actually changed (detected via a content hash) — so your
crawl progress is safe across re-syncs.

## 6. Running the tests

```bash
.venv/bin/python -m pytest
```

## 7. Growing beyond SQLite

SQLite (a single file, `uni_index.db`) is deliberately the starting point: free, zero
setup, extremely reliable, and completely sufficient for a small team pointed at one
shared file (or each running their own copy locally).

You'll outgrow it when you need several people/services **writing to the same index at
the same time** over the network — SQLite is not built for concurrent multi-writer access.
That's roughly when you're moving from "15-30 people sharing files over GitHub" to
"an always-on service other tools talk to." At that point:

- Stand up a small hosted Postgres instance — Supabase, Neon, and Railway all have
  usable free tiers, which matters since your usage is sporadic rather than constant.
- Everything in `db.py` is isolated behind five functions (`connect`, `init_db`,
  `sync_records`, `status_counts`, `mark_status`). Swap the body of `connect()` to open
  a `psycopg`/`psycopg2` connection instead of `sqlite3`, adjust the `?` placeholders in
  the SQL strings to `%s`, and the rest of the codebase (CLI, normalize, tests) doesn't
  need to change, because nothing outside `db.py` knows or cares which database it is.
- Put a thin read API (e.g. FastAPI) in front of that Postgres instance if you want
  people/services to query status without installing this CLI at all.

The canonical file and the git workflow around it don't change at any point in this
growth path — files in a repo with a CI check scale to any number of contributors.

## Design notes (why these choices)

- **Two runtime dependencies** (`click`, `PyYAML`); everything else is the standard library.
  Fewer dependencies, fewer things that break, need upgrading, or cost money. pytest is a
  dev-only extra.
- **Two source layers.** `imported/` is what an external dataset says; `curated/` is what a
  person decided. Keeping them apart means refreshing the import never clobbers a hand edit.
- **Generated files are deterministic** (sorted rows, sorted keys, no timestamps, no
  absolute paths), so re-running a generator on unchanged input produces no git diff.
- **External mess is quarantined at the boundary.** The importer validates with the same
  rules as `normalize` and reports what it skipped, so CI fails only for human errors.
- **The SQL table is operational state, not source data** — it's git-ignored on purpose.
- **A content hash (`source_hash`) per record** makes `sync-db` idempotent: unchanged
  universities keep their crawl status across re-syncs.
