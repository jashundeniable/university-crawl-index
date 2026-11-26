"""
Read-only audit of the whole Phase 1 chain:
    data/sources (imported + curated)  ->  dist/universities.canonical.json  ->  uni_index.db

Sections, in order:
  1. Agreement   - do the layers, the canonical file and the database agree?   (invariants)
  2. Rejects     - what did the importer turn away or merge, by category?      (errors as data)
  3. Identity    - how exposed is the first-domain identity rule?              (sizes the CP2 decision)
  4. Hot files   - where will editing pressure land first?                     (scale)

Writes nothing; opens the database read-only. Exit code 1 if section 1 finds a disagreement.

Usage, from the repo root:
    .venv/bin/python scripts/audit_sources.py
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from uni_index.io_utils import load_layer
from uni_index.normalize import merge_layers  # the same merge `normalize` runs, so the two can't disagree

IMPORTED = Path("data/sources/imported")
CURATED = Path("data/sources/curated")
REPORT = Path("data/reports/hipo_import.yaml")
CANONICAL = Path("dist/universities.canonical.json")
DB = Path(os.environ.get("UNI_INDEX_DB_PATH", "uni_index.db"))
RAW = Path("world_universities.json")  # optional; enables twin lookups and section 3
SHOW = 5  # examples per finding
LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def heading(text: str) -> None:
    print(f"\n{text}\n{'-' * len(text)}")


def key(raw: dict) -> str:
    return str(raw.get("domain") or "").strip().lower()


def host_of(entry: dict) -> str:
    pages = entry.get("web_pages") or []
    return (urlsplit(str(pages[0])).hostname or "").lower() if pages else ""


def covers(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def pct(part: int, whole: int) -> str:
    if not whole:
        return "n/a"
    share = 100 * part / whole
    return "<0.1%" if 0 < share < 0.1 else f"{share:.1f}%"


def category(problem: str) -> str:
    """One bucket per message shape, not per row: blank out the values that vary."""
    shape = re.sub(r"^(\w+) '.*?' ", r"\1 '…' ", problem)       # the offending value
    shape = re.sub(r"'[^'\s]+'(\)?)$", r"'…'\1", shape)          # a trailing quoted value
    return re.sub(r"got -?\d+$", "got …", shape)


def agreement(raw_entries):
    heading("1. Agreement: sources -> canonical -> database")
    problems = []
    imported_layer, curated_layer = load_layer(IMPORTED), load_layer(CURATED)
    _, merge_errors, touched = merge_layers([imported_layer, curated_layer])
    imported = {key(r) for _, r in imported_layer if isinstance(r, dict)}
    sources = imported | {key(r) for _, r in curated_layer if isinstance(r, dict) and key(r)}

    print(f"imported entries        {len(imported)}")
    print(f"curated entries         {len(curated_layer)}: "
          f"{len(touched['overridden'])} override(s), {len(touched['added'])} new")
    for e in merge_errors:
        problems.append(f"merge: {e}")

    for d in sorted(touched["added"]):
        twins = [e for e in raw_entries
                 if d in [str(x).strip().lower() for x in e.get("domains") or []]]
        if twins:
            t = twins[0]
            first = str((t.get("domains") or ["?"])[0]).strip().lower()
            print(f"  new: {d}  <- Hipo lists it under \"{t.get('name')}\", whose identity domain is "
                  f"{first}: the same institution now appears twice")
        elif raw_entries:
            print(f"  new: {d}  <- not in Hipo at all: genuinely new")
        else:
            print(f"  new: {d}  (add {RAW} to check whether Hipo knows it under another domain)")

    if not CANONICAL.exists():
        problems.append(f"{CANONICAL} missing - run `uni-index normalize`")
        return problems
    canonical = {r["domain"] for r in json.loads(CANONICAL.read_text(encoding="utf-8"))}
    print(f"canonical records       {len(canonical)}")
    stale_out = sorted(sources - canonical)
    stale_in = sorted(canonical - sources)
    if stale_out:
        problems.append(f"{len(stale_out)} source domain(s) missing from canonical "
                        f"(invalid, or canonical is stale), e.g. {stale_out[:SHOW]}")
    if stale_in:
        problems.append(f"{len(stale_in)} canonical domain(s) no longer in sources "
                        f"(canonical is stale - re-run normalize), e.g. {stale_in[:SHOW]}")

    if not DB.exists():
        problems.append(f"{DB} missing - run `uni-index sync-db`")
        return problems
    with sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT domain, crawl_status FROM universities").fetchall()
    in_db = {d for d, _ in rows}
    print(f"database rows           {len(in_db)}  "
          f"({', '.join(f'{s} {n}' for s, n in Counter(s for _, s in rows).most_common())})")
    orphans, unsynced = sorted(in_db - canonical), sorted(canonical - in_db)
    if orphans:
        problems.append(f"{len(orphans)} database row(s) not in canonical - sync-db only adds and "
                        f"updates, it never removes: {orphans[:SHOW]}")
    if unsynced:
        problems.append(f"{len(unsynced)} canonical record(s) not in database - run `uni-index sync-db`")
    return problems


def rejects():
    heading("2. Rejects: what the importer turned away or merged")
    if not REPORT.exists():
        print(f"{REPORT} not found - run `uni-index import-hipo` first")
        return
    report = yaml.load(REPORT.read_text(encoding="utf-8"), Loader=LOADER) or {}
    counts = report.get("counts", {})
    print("counts                  " + ", ".join(f"{k} {v}" for k, v in counts.items()))

    categories = Counter()
    example = {}
    for r in report.get("rejected") or []:
        for problem in r.get("problems") or []:
            shape = category(problem)
            categories[shape] += 1
            example.setdefault(shape, r.get("name"))
    for shape, n in categories.most_common():
        print(f"  {n:>5}  {shape}   e.g. {example[shape]}")

    dups = report.get("duplicates") or []
    different = [d for d in dups if str(d.get("name", "")).casefold() != str(d.get("kept", "")).casefold()]
    print(f"duplicates              {len(dups)}: {len(dups) - len(different)} same name, "
          f"{len(different)} different names sharing one domain")
    for d in different[:SHOW]:
        print(f"  {d.get('domain')}: \"{d.get('name')}\"  merged into  \"{d.get('kept')}\"")


def identity(raw_entries, imported):
    heading("3. Identity: how exposed is the first-domain rule?")
    if not raw_entries:
        print(f"{RAW} not found - section skipped")
        return
    kept, seen = [], set()
    for e in raw_entries:  # first occurrence per identity domain, as the importer kept it
        first = str((e.get("domains") or [""])[0]).strip().lower()
        if first in imported and first not in seen:
            seen.add(first)
            kept.append(e)
    multi = [e for e in kept if len(e.get("domains") or []) > 1]
    mismatch = [e for e in kept if host_of(e) and not covers(host_of(e), str(e["domains"][0]).strip().lower())]
    print(f"accepted entries checked {len(kept)}")
    print(f"more than one domain     {len(multi)} ({pct(len(multi), len(kept))}): "
          "identity depended on list order")
    print(f"website outside identity {len(mismatch)} ({pct(len(mismatch), len(kept))}): "
          "the crawler would start from the wrong host")
    for e in mismatch[:SHOW]:
        print(f"  {str(e['domains'][0]).strip().lower():<28} site {host_of(e):<30} {e.get('name')}")


def hot_files():
    heading("4. Hot files: where editing pressure lands first")
    sizes = sorted(((sum(1 for _ in load_layer(p)), p.name) for p in IMPORTED.glob("*.yaml")), reverse=True)
    total = sum(n for n, _ in sizes)
    for n, name in sizes[:SHOW]:
        print(f"  {name:<10} {n:>5}  ({pct(n, total)} of imported)")
    print(f"  {len(sizes)} country files; median {sizes[len(sizes) // 2][0] if sizes else 0} entries")


def main() -> int:
    if not (IMPORTED.is_dir() and CURATED.is_dir()):
        print("Run from the repo root: data/sources/imported and data/sources/curated not found.")
        return 2
    raw_entries = json.loads(RAW.read_text(encoding="utf-8")) if RAW.exists() else []
    problems = agreement(raw_entries)
    rejects()
    identity(raw_entries, {key(r) for _, r in load_layer(IMPORTED) if isinstance(r, dict)})
    hot_files()

    heading("Verdict")
    if problems:
        for p in problems:
            print(f"  DISAGREE  {p}")
        return 1
    print("  sources, canonical and database agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
