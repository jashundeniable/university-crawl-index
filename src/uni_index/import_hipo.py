"""
One time importer: converts a downloaded Hipo 
university-domains-list JSON file into a uni-index source YAML files 
(the same shape I already hand edit in data/universities.source.yaml).

Usage:
    python -m uni_index.import_hipo <path-to-download.json> <path-to-output.yaml>

Example:
    python -m uni-index.import_hipo world_universities.json data/sources/hipo_import.yaml

"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

def convert_entry(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Convert one raw Hipo dataset entry into our source-file schema.
    Returns None if the entry can't be converted (missing domain or country)
    """
    domains = entry.get("domains") or []
    if not domains:
        return None

    domain = str(domains[0]).strip().lower()
    if not domain:
        return None

    country = str(entry.get("alpha_two_code") or "").strip().upper()
    if not country:
        return None

    return {
        "domain": domain,
        "country": country,
        "type": "other",
        "priority": 3,
        "subdomains": [],    
    }

def convert_file(jason_path: Path, out_path: Path) -> Dict [str, int]:
    raw_entries = json.loads(jason_path.read_text(encoding="utf-8"))

    converted: List[Dict[str, Any]] = []
    seen_domains = set()
    stats = {"converted": 0, "skipped_no_domain_or_country": 0, "skipped_duplicate":0}

    for entry in raw_entries:
        record = convert_entry(entry)
        if record is None:
            stats ["skipped_no_domain_or_country"] += 1
            continue
        if record["domain"] in seen_domains:
            stats["skipped_duplicate"] += 1
            continue
        seen_domains.add(record["domain"])
        converted.append(record)
        stats["converted"] += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump({"universities": converted}, f, sort_keys=False, allow_unicode=True)

    return stats

def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python -m uni_index.import_hipo <input.json> <output.yaml>")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    if not json_path.exists():
        print(f"Input file not found: {json_path}")
        sys.exit(1)

    stats = convert_file(json_path, out_path)
    print(
        f"Converted {stats['converted']} universities -> {out_path}\n"
        f"Skipped {stats['skipped_no_domain_or_country']} (no usable domain/country), "
        f"{stats['skipped_duplicate']} (duplicate domain in source file)"
    )


if __name__ == "__main__":
    main()