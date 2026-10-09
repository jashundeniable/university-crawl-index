import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(1, str(project_root))

from uni_index.import_hipo import convert_entry  # pyright: ignore[reportMissingImports]


def test_happy_path():
    entry = {
        "name": "Sabanci University",
        "alpha_two_code": "TR",
        "domains": ["sabanciuniv.edu", "sabanciuniv.edu.tr"],
    }

    result = convert_entry(entry)

    assert result == {
        "domain": "sabanciuniv.edu",
        "country": "TR",
        "type": "other",
        "priority": 3,
        "subdomains": [],
    }


def test_takes_first_domain_only():
    entry = {
        "alpha_two_code": "US",
        "domains": ["mit.edu", "web.mit.edu"],
    }

    result = convert_entry(entry)

    assert result["domain"] == "mit.edu"


def test_lowercases_and_strips_domain():
    entry = {
        "alpha_two_code": "US",
        "domains": ["  MIT.EDU  "],
    }

    result = convert_entry(entry)

    assert result["domain"] == "mit.edu"


def test_uppercases_country_code():
    entry = {
        "alpha_two_code": "us",
        "domains": ["mit.edu"],
    }

    result = convert_entry(entry)

    assert result["country"] == "US"


def test_missing_domains_returns_none():
    entry = {
        "alpha_two_code": "US",
    }

    result = convert_entry(entry)

    assert result is None
