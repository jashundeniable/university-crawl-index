"""Loading source CSV/YAML files, writing per-country source files, and the canonical JSON."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import yaml

SOURCE_SUFFIXES = {".csv", ".yaml", ".yml"}

# libyaml's C parser is roughly 10x faster than PyYAML's pure-Python one and yields identical
# data, so it is used whenever installed (the standard PyYAML wheels include it).
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

# (origin, raw record). origin reads "path/to/file.yaml row 3", so every error points at its data.
SourceRecord = Tuple[str, Any]


def load_source_records(path: Path) -> List[Any]:
    """One source file -> its raw records."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _load_csv(path)
    if suffix in (".yaml", ".yml"):
        return _load_yaml(path)
    raise ValueError(f"Unsupported source file type '{suffix}'. Use .csv, .yaml, or .yml")


def source_files(path: Path) -> List[Path]:
    """A file -> [that file]. A folder -> the source files directly inside it, sorted."""
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise ValueError(f"Source path '{path}' does not exist")
    files = sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() in SOURCE_SUFFIXES)
    if not files:
        # an empty folder must never quietly produce an empty index
        raise ValueError(f"No .yaml/.yml/.csv files directly inside '{path}'")
    return files


def load_layer(path: Path) -> List[SourceRecord]:
    """Every raw record in a file or folder, each tagged with where it came from."""
    return [
        (f"{file} row {i}", raw)
        for file in source_files(path)
        for i, raw in enumerate(load_source_records(file), start=1)
    ]


def _load_csv(path: Path) -> List[Any]:
    with path.open(newline="", encoding="utf-8") as f:
        return [dict(row) for row in csv.DictReader(f)]


def _load_yaml(path: Path) -> List[Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.load(f, Loader=_YAML_LOADER) or []  # a *safe* loader, despite yaml.load
    # either a bare list, or a mapping with a "universities:" key
    if isinstance(data, dict):
        data = data.get("universities") or []
    if not isinstance(data, list):
        raise ValueError(f"{path}: must be a list of records (optionally under a 'universities' key)")
    return data


def write_by_country(records: Iterable[Dict[str, Any]], out_dir: Path, header: str) -> Dict[str, int]:
    """
    Write records into out_dir/<country>.yaml, one file per 2-letter code, rows sorted by
    domain - so regenerating from unchanged input yields byte-identical files (no git noise).
    """
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rec in records:
        groups[str(rec["country"]).strip().lower()].append(rec)

    out_dir.mkdir(parents=True, exist_ok=True)
    for code, rows in sorted(groups.items()):
        rows.sort(key=lambda r: str(r.get("domain", "")).strip().lower())
        body = yaml.safe_dump({"universities": rows}, sort_keys=False, allow_unicode=True)
        (out_dir / f"{code}.yaml").write_text(header + body, encoding="utf-8")
    return {code: len(rows) for code, rows in sorted(groups.items())}


def write_canonical_json(records: List[Dict[str, Any]], out_path: Path) -> None:
    """Deterministic output (sorted by domain, sorted keys, trailing newline) => clean git diffs."""
    records_sorted = sorted(records, key=lambda r: r["domain"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(records_sorted, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")
