"""Command-line entrypoint. After an editable install this is available as `uni-index`."""
from __future__ import annotations

import json
import os
from pathlib import Path

import click

from .db import connect, init_db, mark_status, status_counts, sync_records, total_count
from .normalize import normalize_file

DEFAULT_CANONICAL_PATH = Path("dist/universities.canonical.json")
DEFAULT_DB_PATH = os.environ.get("UNI_INDEX_DB_PATH", "uni_index.db")


@click.group()
@click.version_option(package_name="uni-index")
def cli():
    """Manage the canonical university crawl-target index."""


@cli.command()
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--out", type=click.Path(path_type=Path), default=DEFAULT_CANONICAL_PATH,
    show_default=True, help="Where to write the canonical JSON.",
)
def normalize(source: Path, out: Path):
    """Normalize a source CSV/YAML file into the canonical, versioned JSON index."""
    records, errors = normalize_file(source, out)

    if errors:
        click.secho(f"{len(errors)} row(s) skipped due to errors:", fg="yellow")
        for e in errors:
            click.echo(f"  - {e}")

    click.secho(f"Wrote {len(records)} canonical record(s) to {out}", fg="green")

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
