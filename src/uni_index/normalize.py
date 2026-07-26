"""Turns a raw source file into the canonical, versioned JSON index."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .io_utils import load_source_records, write_canonical_json
from .models import ValidationErrorList, build_canonical_university


def compute_source_hash(record: Dict[str, Any]) -> str:
    """
    Short, stable fingerprint of a canonical record's content.
    Used later to detect "did anything actually change" without a full field-by-field diff.
    """
    payload = json.dumps(record, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def normalize_file(source_path: Path, out_path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Reads `source_path` (.csv/.yaml/.yml), validates + normalizes every row, writes the
    canonical JSON to `out_path`, and returns (canonical_records, error_messages).

    Rows with errors are skipped (not written), and every error is collected rather than
    stopping at the first one, so a single run tells you everything that needs fixing.
    """
    raw_records = load_source_records(source_path)
    canonical: List[Dict[str, Any]] = []
    errors: List[str] = []
    seen_domains = set()

    for i, raw in enumerate(raw_records, start=1):
        label = raw.get("domain", "?")
        try:
            record = build_canonical_university(raw)
        except ValidationErrorList as e:
            errors.extend(f"Row {i} ({label}): {p}" for p in e.problems)
            continue

        if record.domain in seen_domains:
            errors.append(f"Row {i}: duplicate domain '{record.domain}' already seen in this file")
            continue
        seen_domains.add(record.domain)

        record_dict = record.to_dict()
        record_dict["source_hash"] = compute_source_hash(record_dict)
        canonical.append(record_dict)

    canonical.sort(key=lambda r: r["domain"])
    write_canonical_json(canonical, out_path)
    return canonical, errors
