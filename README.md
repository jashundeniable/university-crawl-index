# University Crawl Index

A small CLI that turns a human-edited list of target universities into:

1. A **canonical, versioned JSON file** (`dist/universities.canonical.json`) — validated,
   deduped, deterministically sorted. This is committed to git and is the stable
   source of truth every crawl run reads from.
2. A **SQL "master index"** (SQLite, one file: `uni_index.db`) that dedupes universities
   by domain and tracks crawl status over time.

## How it fits together

```
data/universities.source.yaml   <- you edit this (human-friendly list, CSV also supported)
        │
        │  uni-index normalize
        ▼
dist/universities.canonical.json  <- generated: validated, deduped, sorted — commit this
        │        (a GitHub Action fails the build if you forget to regenerate it)
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

## 1. One-time setup

```bash
# 1. Clone your repo and open a terminal in it
git clone <your-repo-url>
cd <your-repo-folder>

# 2. Create an isolated Python environment (keeps this project's packages separate
#    from anything else on your machine)
python3 -m venv .venv
source .venv/bin/activate        # on Windows (PowerShell): .venv\Scripts\Activate.ps1

# 3. Install this project + its two dependencies (Click for the CLI, PyYAML for parsing)
pip install -e .
```

You only need to redo step 3 when dependencies change. Steps 1-2 are one-time per machine.
Each time you come back to work on this, you just need `source .venv/bin/activate` again.

## 2. Day-to-day usage

Edit `data/universities.source.yaml` (add/update universities), then:

```bash
# Turn the source file into the canonical, versioned JSON
uni-index normalize data/universities.source.yaml

# Load the canonical file into the SQL master index (dedupes + tracks crawl status)
uni-index sync-db

# See a quick status summary
uni-index status
```

Commit **both** your source-file edit **and** the regenerated
`dist/universities.canonical.json` in the same PR/commit. `.github/workflows/validate.yml`
will fail CI if you forget to regenerate it, so the canonical file can never silently
drift out of sync with the source — this is what makes it trustworthy as a "source of truth"
once you have more than one person contributing.

## 3. Adding a university

Open `data/universities.source.yaml` and add an entry:

```yaml
  - domain: example.edu
    country: US        # 2-letter code
    type: public        # public | private | community | research | other
    priority: 2          # 1 (crawl first) .. 5 (crawl last), default 3
    subdomains:
      - admissions.example.edu
```

CSV works too, with the same column names (`subdomains` separated by `|`, `;`, or `,`):

```csv
domain,country,type,priority,subdomains
example.edu,US,public,2,admissions.example.edu|apply.example.edu
```

Then run `uni-index normalize <your file>`, check the messages, commit.

Invalid rows are **skipped, not silently accepted** — you'll see exactly what's wrong:

```
$ uni-index normalize data/universities.source.yaml
2 row(s) skipped due to errors:
  - Row 3 (fake-school): domain 'fake school' doesn't look like a valid domain (e.g. 'mit.edu')
  - Row 5 (mit.edu): subdomain 'admissions.harvard.edu' must end with the university domain 'mit.edu'
Wrote 4 canonical record(s) to dist/universities.canonical.json
```

## 4. Reporting crawl results back (for whatever crawls the list)

```bash
uni-index mark mit.edu --status in_progress
uni-index mark mit.edu --status done
uni-index mark mit.edu --status failed --error "connection timeout"
```

Re-running `sync-db` later **won't** reset a domain's status back to `pending` unless
that domain's canonical record actually changed (detected via a content hash) — so your
crawl progress is safe across re-syncs.

## 5. Running the tests

```bash
python -m unittest discover -s tests -v
```

No extra install needed — the tests use only the standard library. (If you'd rather use
`pytest`, `pip install pytest` and just run `pytest` — it can run these same files as-is.)

## 6. Growing beyond SQLite

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
  (If the schema grows a lot more complex at that point, this is also a natural moment
  to introduce an ORM like SQLAlchemy — not needed for a "simple table," genuinely
  useful once you have several related tables.)
- Put a thin read API (e.g. FastAPI) in front of that Postgres instance if you want
  people/services to query status without installing this CLI at all.

The `dist/universities.canonical.json` file and the git workflow around it don't change
at any point in this growth path — that part scales to any number of contributors as-is,
because it's just files in a repo with a CI check.

## Design notes (why these choices)

- **Only two dependencies** (`click`, `PyYAML`), everything else is Python's standard
  library (`sqlite3`, `dataclasses`, `unittest`, `hashlib`, `json`). Fewer dependencies
  means fewer things that can break, need upgrading, or cost money — directly in service
  of "cheap, stable, reliable."
- **The canonical JSON is a build artifact, not hand-edited.** Humans edit
  `data/universities.source.yaml`; the CLI regenerates the canonical file deterministically
  (sorted keys, sorted rows, no timestamps) so diffs in PRs are small and meaningful.
- **The SQL table is operational state, not source data** — it's git-ignored on purpose.
  Crawl status changes constantly and shouldn't live in version control; the canonical
  file is what's versioned.
- **A content hash (`source_hash`) per record** means re-running `sync-db` is safe and
  idempotent: unchanged universities are left completely alone (including their crawl
  status), so nothing gets accidentally reset to `pending` just because you re-ran a command.
