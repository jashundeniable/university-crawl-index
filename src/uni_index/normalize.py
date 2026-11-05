"""Merges layered source files into the canonical, versioned JSON index."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set, Tuple

from .io_utils import SourceRecord, load_layer, write_canonical_json
from .models import ValidationErrorList, build_canonical_university


def compute_source_hash(record: Dict[str, Any]) -> str:
    """Short, stable fingerprint of a canonical record's content (cheap "did it change?")."""
    payload = json.dumps(record, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _domain_key(raw: Dict[str, Any]) -> str:
    # mirrors how models.py normalizes `domain`, so "MIT.EDU" in one layer meets "mit.edu" in another
    return str(raw.get("domain") or "").strip().lower()


def _specified(value: Any) -> bool:
    # an empty YAML field (None) or an empty CSV cell ("") means "not set here": it never overrides
    return value is not None and value != ""


def merge_layers(layers: Sequence[List[SourceRecord]]) -> Tuple[List[SourceRecord], List[str], Dict[str, Set[str]]]:
    """
    Later layers override earlier ones field by field, matched by domain.
    A domain repeated *within* one layer is an error (the first occurrence is kept).

    Returns (merged records, errors, {"overridden": domains, "added": domains first seen in a later layer}).
    """
    merged: Dict[str, SourceRecord] = {}
    unkeyed: List[SourceRecord] = []
    errors: List[str] = []
    touched: Dict[str, Set[str]] = {"overridden": set(), "added": set()}

    for index, layer in enumerate(layers):
        first_seen: Dict[str, str] = {}
        for origin, raw in layer:
            if not isinstance(raw, dict):
                errors.append(f"{origin}: expected a mapping of fields, got {type(raw).__name__}")
                continue
            key = _domain_key(raw)
            if not key:
                unkeyed.append((origin, raw))  # validation reports the missing domain
                continue
            if key in first_seen:
                errors.append(f"{origin}: duplicate domain '{key}' (already defined at {first_seen[key]})")
                continue
            first_seen[key] = origin

            if key in merged:
                base_origin, base = merged[key]
                overrides = {k: v for k, v in raw.items() if _specified(v)}
                merged[key] = (f"{origin} (overriding {base_origin})", {**base, **overrides})
                touched["overridden"].add(key)
            else:
                merged[key] = (origin, dict(raw))
                if index > 0:
                    touched["added"].add(key)

    return list(merged.values()) + unkeyed, errors, touched


def normalize_sources(
    sources: Sequence[Path], out_path: Path
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, int]]:
    """
    Merge `sources` (files or folders, in precedence order: later wins), validate every merged
    record, write the canonical JSON to `out_path`.

    Returns (canonical records, errors, {"overrides": n, "added_by_later_layers": n}).
    Invalid records are skipped, never silently repaired; every problem is collected, so one
    run shows everything that needs attention.
    """
    layers = [load_layer(Path(s)) for s in sources]
    candidates, errors, touched = merge_layers(layers)

    canonical: List[Dict[str, Any]] = []
    for origin, raw in candidates:
        label = raw.get("domain") or "?"
        try:
            record = build_canonical_university(raw)
        except ValidationErrorList as e:
            problems = list(e.problems)
            if _domain_key(raw) in touched["added"] and any("country is required" in p for p in problems):
                problems.append(
                    "no earlier layer defines this domain - to override an imported entry use its "
                    "exact domain; to add a new one include every required field"
                )
            errors.extend(f"{origin} ({label}): {p}" for p in problems)
            continue
        record_dict = record.to_dict()
        record_dict["source_hash"] = compute_source_hash(record_dict)
        canonical.append(record_dict)

    canonical.sort(key=lambda r: r["domain"])
    write_canonical_json(canonical, out_path)
    stats = {"overrides": len(touched["overridden"]), "added_by_later_layers": len(touched["added"])}
    return canonical, errors, stats


def normalize_file(source_path: Path, out_path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Single-source shorthand, kept so existing callers and tests work unchanged."""
    records, errors, _ = normalize_sources([source_path], out_path)
    return records, errors
