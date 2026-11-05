"""Command-line entrypoint. After an editable install this is available as `uni-index`."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Tuple

import click

from .db import connect, init_db, mark_status, status_counts, sync_records, total_count
from .import_hipo import import_hipo
from .io_utils import load_source_records, write_by_country
from .normalize import normalize_sources

IMPORTED_DIR = Path("data/sources/imported")
CURATED_DIR = Path("data/sources/curated")
DEFAULT_SOURCES = (IMPORTED_DIR, CURATED_DIR)  # precedence order: later wins
DEFAULT_REPORT_PATH = Path("data/reports/hipo_import.yaml")
DEFAULT_CANONICAL_PATH = Path("dist/universities.canonical.json")
DEFAULT_DB_PATH = os.environ.get("UNI_INDEX_DB_PATH", "uni_index.db")

CURATED_HEADER = (
    "# CURATED - hand-edited. Overrides data/sources/imported/ field by field, matched by domain.\n"
    "# To override an imported entry: its exact domain plus only the fields you want changed.\n"
    "# Fields: domain, country (2-letter), type (public|private|community|research|other),\n"
    "#         priority (1 = crawl first .. 5 = crawl last), subdomains (list).\n"
)


@click.group()
@click.version_option(package_name="uni-index")
def cli():
    """Manage the canonical university crawl-target index."""


@cli.command("import-hipo")
@click.argument("json_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", "out_dir", type=click.Path(file_okay=False, path_type=Path),
              default=IMPORTED_DIR, show_default=True, help="Folder for the generated country files.")
@click.option("--report", "report_path", type=click.Path(dir_okay=False, path_type=Path),
              default=DEFAULT_REPORT_PATH, show_default=True, help="Where to record skipped entries.")
def import_hipo_command(json_path: Path, out_dir: Path, report_path: Path):
    """Turn a downloaded Hipo JSON file into generated, per-country source files."""
    try:
        stats = import_hipo(json_path, out_dir, report_path)
    except ValueError as e:  # includes malformed JSON, e.g. an error page saved as data
        raise click.ClickException(f"{json_path}: {e}")

    click.secho(
        f"Imported {stats['accepted']} of {stats['total']} entries into "
        f"{stats['countries']} country files in {out_dir}",
        fg="green",
    )
    if stats["rejected"] or stats["duplicates"]:
        click.secho(
            f"Skipped {stats['rejected']} invalid and {stats['duplicates']} duplicate "
            f"entries - see {report_path}",
            fg="yellow",
        )


@cli.command()
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", "out_dir", type=click.Path(file_okay=False, path_type=Path),
              default=CURATED_DIR, show_default=True, help="Folder for the per-country files.")
@click.option("--force", is_flag=True, help="Overwrite country files that already exist.")
def split(source: Path, out_dir: Path, force: bool):
    """Split one hand-edited source file into per-country curated files."""
    try:
        records = load_source_records(source)
    except ValueError as e:
        raise click.ClickException(str(e))

    problems = []
    for i, raw in enumerate(records, start=1):
        code = str(raw.get("country") or "").strip() if isinstance(raw, dict) else ""
        if len(code) != 2 or not code.isalpha():
            problems.append(f"{source} row {i}: needs a 2-letter country code to be split")
    if problems:
        raise click.ClickException("\n".join(problems))

    targets = sorted({str(r["country"]).strip().lower() + ".yaml" for r in records})
    clashes = [t for t in targets if (out_dir / t).exists()]
    if clashes and not force:
        raise click.ClickException(
            f"Refusing to overwrite existing file(s) in {out_dir}: {', '.join(clashes)} "
            "(they may hold hand edits; add --force if you're sure)"
        )

    counts = write_by_country(records, out_dir, CURATED_HEADER)
    for code, n in counts.items():
        click.echo(f"  {out_dir / (code + '.yaml')}: {n} record(s)")
    click.secho(f"Split {sum(counts.values())} record(s) into {len(counts)} file(s)", fg="green")


@cli.command()
@click.argument("sources", nargs=-1, type=click.Path(exists=True, path_type=Path))
@click.option("--out", type=click.Path(path_type=Path), default=DEFAULT_CANONICAL_PATH,
              show_default=True, help="Where to write the canonical JSON.")
def normalize(sources: Tuple[Path, ...], out: Path):
    """
    Merge SOURCES (files or folders; later ones override earlier) into the canonical JSON.

    With no SOURCES: data/sources/imported, then data/sources/curated.
    """
    if not sources:
        missing = [str(p) for p in DEFAULT_SOURCES if not p.is_dir()]
        if missing:
            raise click.ClickException(
                f"Default source folder(s) not found: {', '.join(missing)} - "
                "run from the repo root, or pass source paths explicitly."
            )
        sources = DEFAULT_SOURCES

    try:
        records, errors, stats = normalize_sources(list(sources), out)
    except ValueError as e:
        raise click.ClickException(str(e))

    if errors:
        click.secho(f"{len(errors)} problem(s) found - affected rows were skipped:", fg="yellow")
        for e in errors:
            click.echo(f"  - {e}")

    click.secho(f"Wrote {len(records)} canonical record(s) to {out}", fg="green")
    if len(sources) > 1:
        click.echo(
            f"  later layers: {stats['overrides']} override(s), "
            f"{stats['added_by_later_layers']} new entr(ies)"
        )

    if errors:
        raise SystemExit(1)


@cli.command("sync-db")
@click.option(
    "--canonical", type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=DEFAULT_CANONICAL_PATH, show_default=True, help="Path to the canonical JSON file.",
)
@click.option(
    "--db-path", default=DEFAULT_DB_PATH, show_default=True,
    help="SQLite file path (or set the UNI_INDEX_DB_PATH env var).",
)
def sync_db(canonical: Path, db_path: str):
    """Upsert the canonical JSON index into the SQL master index (dedupes + tracks crawl status)."""
    records = json.loads(canonical.read_text(encoding="utf-8"))

    with connect(db_path) as conn:
        init_db(conn)
        stats = sync_records(conn, records)

    click.secho(
        f"Synced: {stats['inserted']} inserted, {stats['updated']} updated, "
        f"{stats['unchanged']} unchanged",
        fg="green",
    )


@cli.command()
@click.option("--db-path", default=DEFAULT_DB_PATH, show_default=True)
def status(db_path: str):
    """Show a summary of crawl status from the master index."""
    with connect(db_path) as conn:
        init_db(conn)
        counts = status_counts(conn)
        total = total_count(conn)

    if total == 0:
        click.secho("No records yet. Run 'normalize' then 'sync-db' first.", fg="yellow")
        return

    click.echo("Crawl status summary:")
    for name, n in counts.items():
        click.echo(f"  {name:<12} {n}")
    click.echo(f"  {'TOTAL':<12} {total}")


@cli.command()
@click.argument("domain")
@click.option(
    "--status", "crawl_status", required=True,
    type=click.Choice(["pending", "in_progress", "done", "failed"]),
    help="New crawl status for this domain.",
)
@click.option("--error", default=None, help="Optional error message (used with --status failed).")
@click.option("--db-path", default=DEFAULT_DB_PATH, show_default=True)
def mark(domain: str, crawl_status: str, error: str, db_path: str):
    """Update the crawl status for a single domain. Intended to be called by your crawler."""
    with connect(db_path) as conn:
        init_db(conn)
        found = mark_status(conn, domain, crawl_status, error)

    if not found:
        click.secho(f"No university found with domain '{domain}'", fg="red")
        raise SystemExit(1)

    click.secho(f"Marked {domain} as {crawl_status}", fg="green")


if __name__ == "__main__":
    cli()
