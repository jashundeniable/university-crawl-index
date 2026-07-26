"""Loading source CSV/YAML files and writing the canonical JSON output."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List

import yaml


def load_source_records(path: Path) -> List[Dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _load_csv(path)
    if suffix in (".yaml", ".yml"):
        return _load_yaml(path)
    raise ValueError(f"Unsupported source file type '{suffix}'. Use .csv, .yaml, or .yml")


def _load_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def _load_yaml(path: Path) -> List[Dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or []
    # allow either a bare list, or a dict with a "universities:" key (used in the example file)
    if isinstance(data, dict):
        data = data.get("universities", [])
    if not isinstance(data, list):
        raise ValueError(
            "YAML source must be a list of records (optionally nested under a 'universities' key)"
        )
    return data


def write_canonical_json(records: List[Dict[str, Any]], out_path: Path) -> None:
    """Deterministic output (sorted by domain, sorted keys, trailing newline) => clean git diffs."""
    records_sorted = sorted(records, key=lambda r: r["domain"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(records_sorted, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")
