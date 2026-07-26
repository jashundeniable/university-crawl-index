"""
Canonical university record: the shape every source row gets normalized into.

Deliberately dependency-free (stdlib `dataclasses` only) so this file has nothing
to break as Python versions or packages change.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# Reasonably strict hostname pattern: labels of 1-63 chars, no leading/trailing
# hyphen per label, at least two labels (so "mit.edu" passes, "mit" does not).
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)"
    r"(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)

# Adjust this to fit your own taxonomy — it's just a controlled vocabulary check.
VALID_TYPES = {"public", "private", "community", "research", "other"}

# 1 = crawl first (highest priority) ... 5 = crawl last (lowest priority)
MIN_PRIORITY, MAX_PRIORITY, DEFAULT_PRIORITY = 1, 5, 3


class ValidationErrorList(Exception):
    """Raised when a raw row fails validation. Carries all problems found, not just the first."""

    def __init__(self, problems: List[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass
class CanonicalUniversity:
    domain: str
    country: str
    type: str = "other"
    subdomains: List[str] = field(default_factory=list)
    priority: int = DEFAULT_PRIORITY

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clean_subdomains_input(raw_subs: Any) -> List[str]:
    """Accepts a YAML list OR a delimited string (from a CSV cell) and returns a list of strings."""
    if raw_subs is None:
        return []
    if isinstance(raw_subs, str):
        raw_subs = re.split(r"[|;,]", raw_subs)
    if not isinstance(raw_subs, list):
        return [str(raw_subs)]
    return [str(s) for s in raw_subs]


def build_canonical_university(raw: Dict[str, Any]) -> CanonicalUniversity:
    """
    Validate + normalize a raw dict (from a CSV row or YAML entry) into a CanonicalUniversity.

    Raises ValidationErrorList (with *all* problems found) rather than stopping at the
    first error, so a novice fixing the source file sees everything wrong in one pass.
    """
    problems: List[str] = []

    # --- domain (also the dedupe key) ---
    domain = str(raw.get("domain") or "").strip().lower()
    if not domain:
        problems.append("domain is required")
    elif not DOMAIN_RE.match(domain):
        problems.append(f"domain '{domain}' doesn't look like a valid domain (e.g. 'mit.edu')")

    # --- country ---
    country = str(raw.get("country") or "").strip().upper()
    if not country:
        problems.append("country is required (2-letter code, e.g. 'US', 'GB', 'IN')")
    elif len(country) != 2 or not country.isalpha():
        problems.append(f"country '{country}' must be a 2-letter code, e.g. 'US', 'GB', 'IN'")

    # --- type ---
    type_ = str(raw.get("type") or "other").strip().lower()
    if type_ not in VALID_TYPES:
        problems.append(f"type '{type_}' must be one of {sorted(VALID_TYPES)}")

    # --- priority ---
    raw_priority = raw.get("priority", DEFAULT_PRIORITY)
    priority = DEFAULT_PRIORITY
    try:
        priority = int(raw_priority)
        if not (MIN_PRIORITY <= priority <= MAX_PRIORITY):
            problems.append(
                f"priority must be between {MIN_PRIORITY} (crawl first) and "
                f"{MAX_PRIORITY} (crawl last), got {priority}"
            )
    except (TypeError, ValueError):
        problems.append(f"priority '{raw_priority}' must be a whole number "
                         f"{MIN_PRIORITY}-{MAX_PRIORITY}")

    # --- subdomains (only cross-checked against domain if domain itself is valid) ---
    cleaned_subs = sorted({
        s.strip().lower() for s in _clean_subdomains_input(raw.get("subdomains")) if s.strip()
    })
    if domain and DOMAIN_RE.match(domain):
        for s in cleaned_subs:
            if not DOMAIN_RE.match(s):
                problems.append(f"subdomain '{s}' doesn't look like a valid hostname")
            elif not (s == domain or s.endswith("." + domain)):
                problems.append(f"subdomain '{s}' must end with the university domain '{domain}'")

    if problems:
        raise ValidationErrorList(problems)

    return CanonicalUniversity(
        domain=domain,
        country=country,
        type=type_,
        subdomains=cleaned_subs,
        priority=priority,
    )
