"""Layered, per-country sources: loading, merging, importing, splitting."""
import json
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

from uni_index.cli import cli
from uni_index.import_hipo import import_hipo
from uni_index.normalize import normalize_sources


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


HIPO_SAMPLE = [
    {"name": "Massachusetts Institute of Technology", "domains": ["mit.edu"],
     "alpha_two_code": "US", "country": "United States", "web_pages": ["https://web.mit.edu/"]},
    {"name": "Johns Hopkins University", "domains": ["jh.edu", "jhu.edu"],
     "alpha_two_code": "US", "country": "United States", "web_pages": ["http://www.jhu.edu/"]},
    {"name": "Indian Institute of Technology Bombay", "domains": ["iitb.ac.in"],
     "alpha_two_code": "IN", "country": "India", "web_pages": ["https://www.iitb.ac.in/"]},
    {"name": "MIT, listed twice", "domains": ["mit.edu"], "alpha_two_code": "US", "web_pages": []},
    {"name": "Broken Domain College", "domains": ["not a domain"], "alpha_two_code": "US", "web_pages": []},
    {"name": "No Country University", "domains": ["nocountry.edu"], "country": "Somewhere", "web_pages": []},
    {"name": "No Domain University", "domains": [], "alpha_two_code": "GB", "web_pages": []},
]


class TempDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.imported = self.root / "imported"
        self.curated = self.root / "curated"
        self.out = self.root / "canonical.json"

    def tearDown(self):
        self._tmp.cleanup()


class TestLayers(TempDir):
    def test_folder_layer_loads_every_file(self):
        write(self.imported / "us.yaml", "universities:\n  - {domain: mit.edu, country: US}\n")
        write(self.imported / "gb.yaml", "universities:\n  - {domain: ox.ac.uk, country: GB}\n")
        records, errors, _ = normalize_sources([self.imported], self.out)
        self.assertEqual(errors, [])
        self.assertEqual([r["domain"] for r in records], ["mit.edu", "ox.ac.uk"])

    def test_later_layer_overrides_field_by_field(self):
        write(self.imported / "us.yaml",
              "universities:\n  - {domain: mit.edu, country: US, type: other, priority: 3}\n")
        write(self.curated / "us.yaml", "universities:\n  - {domain: MIT.EDU, priority: 1}\n")
        records, errors, stats = normalize_sources([self.imported, self.curated], self.out)
        self.assertEqual(errors, [])
        (mit,) = records
        self.assertEqual((mit["priority"], mit["type"], mit["country"]), (1, "other", "US"))
        self.assertEqual(stats, {"overrides": 1, "added_by_later_layers": 0})

    def test_empty_yaml_values_never_override(self):
        write(self.imported / "us.yaml",
              "universities:\n  - {domain: mit.edu, country: US, type: private, priority: 1}\n")
        write(self.curated / "us.yaml", "universities:\n  - {domain: mit.edu, type: null, priority: ''}\n")
        records, errors, _ = normalize_sources([self.imported, self.curated], self.out)
        self.assertEqual(errors, [])
        self.assertEqual((records[0]["type"], records[0]["priority"]), ("private", 1))

    def test_empty_csv_cells_never_override(self):
        write(self.imported / "us.yaml",
              "universities:\n  - {domain: mit.edu, country: US, type: private, priority: 1}\n")
        write(self.curated / "us.csv", "domain,country,type,priority,subdomains\nmit.edu,,,,\n")
        records, errors, _ = normalize_sources([self.imported, self.curated], self.out)
        self.assertEqual(errors, [])
        self.assertEqual((records[0]["type"], records[0]["priority"]), ("private", 1))

    def test_later_layer_can_add_new_entries(self):
        write(self.imported / "us.yaml", "universities:\n  - {domain: mit.edu, country: US}\n")
        write(self.curated / "in.yaml", "universities:\n  - {domain: iitb.ac.in, country: IN}\n")
        records, errors, stats = normalize_sources([self.imported, self.curated], self.out)
        self.assertEqual(errors, [])
        self.assertEqual(len(records), 2)
        self.assertEqual(stats, {"overrides": 0, "added_by_later_layers": 1})

    def test_duplicate_within_one_layer_is_an_error(self):
        write(self.curated / "gb.yaml", "universities:\n  - {domain: mit.edu, country: GB}\n")
        write(self.curated / "us.yaml", "universities:\n  - {domain: mit.edu, country: US}\n")
        records, errors, _ = normalize_sources([self.curated], self.out)
        self.assertEqual(len(records), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("duplicate domain", errors[0])
        self.assertIn("gb.yaml", errors[0])  # points back at the first definition
        self.assertIn("us.yaml", errors[0])

    def test_partial_entry_matching_nothing_explains_itself(self):
        write(self.imported / "us.yaml", "universities:\n  - {domain: mit.edu, country: US}\n")
        write(self.curated / "us.yaml", "universities:\n  - {domain: mitt.edu, priority: 1}\n")
        records, errors, _ = normalize_sources([self.imported, self.curated], self.out)
        self.assertEqual([r["domain"] for r in records], ["mit.edu"])
        joined = "\n".join(errors)
        self.assertIn("country is required", joined)
        self.assertIn("no earlier layer defines this domain", joined)

    def test_empty_folder_fails_loudly_and_writes_nothing(self):
        self.imported.mkdir()
        with self.assertRaises(ValueError):
            normalize_sources([self.imported], self.out)
        self.assertFalse(self.out.exists())

    def test_non_mapping_entry_is_reported_not_crashed(self):
        write(self.imported / "us.yaml",
              "universities:\n  - just-a-string\n  - {domain: mit.edu, country: US}\n")
        records, errors, _ = normalize_sources([self.imported], self.out)
        self.assertEqual(len(records), 1)
        self.assertIn("expected a mapping", errors[0])


class TestImportHipo(TempDir):
    def run_import(self):
        src = write(self.root / "world_universities.json", json.dumps(HIPO_SAMPLE))
        report = self.root / "reports" / "hipo_import.yaml"
        return import_hipo(src, self.imported, report), report

    def test_one_file_per_country_with_generated_banner(self):
        stats, _ = self.run_import()
        self.assertEqual(sorted(p.name for p in self.imported.iterdir()), ["in.yaml", "us.yaml"])
        us = (self.imported / "us.yaml").read_text()
        self.assertTrue(us.startswith("# GENERATED"))
        self.assertLess(us.index("jh.edu"), us.index("mit.edu"))  # rows sorted by domain
        self.assertEqual(stats["countries"], 2)

    def test_bad_rows_are_quarantined_in_the_report(self):
        stats, report = self.run_import()
        self.assertEqual((stats["accepted"], stats["rejected"], stats["duplicates"]), (3, 3, 1))
        text = report.read_text()
        for name in ("Broken Domain College", "No Country University",
                     "No Domain University", "MIT, listed twice"):
            self.assertIn(name, text)

    def test_imported_layer_normalizes_cleanly(self):
        self.run_import()
        records, errors, _ = normalize_sources([self.imported], self.out)
        self.assertEqual(errors, [])
        self.assertEqual(len(records), 3)

    def test_reimport_is_byte_identical(self):
        self.run_import()
        first = {p.name: p.read_bytes() for p in self.imported.iterdir()}
        self.run_import()
        second = {p.name: p.read_bytes() for p in self.imported.iterdir()}
        self.assertEqual(first, second)

    def test_stale_country_files_are_cleared(self):
        write(self.imported / "zz.yaml", "universities: []\n")
        self.run_import()
        self.assertFalse((self.imported / "zz.yaml").exists())

    def test_error_page_saved_as_json_fails_loudly(self):
        src = write(self.root / "world_universities.json", "404: Not Found")
        with self.assertRaises(ValueError):
            import_hipo(src, self.imported, self.root / "report.yaml")


class TestCli(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def test_split_then_refuses_to_clobber_hand_edits(self):
        with self.runner.isolated_filesystem():
            write(Path("source.yaml"),
                  "universities:\n  - {domain: mit.edu, country: US}\n  - {domain: ox.ac.uk, country: gb}\n")
            first = self.runner.invoke(cli, ["split", "source.yaml", "--out", "curated"])
            self.assertEqual(first.exit_code, 0, first.output)
            self.assertEqual(sorted(p.name for p in Path("curated").iterdir()), ["gb.yaml", "us.yaml"])
            self.assertTrue(Path("curated/us.yaml").read_text().startswith("# CURATED"))
            again = self.runner.invoke(cli, ["split", "source.yaml", "--out", "curated"])
            self.assertNotEqual(again.exit_code, 0)
            self.assertIn("Refusing to overwrite", again.output)
            forced = self.runner.invoke(cli, ["split", "source.yaml", "--out", "curated", "--force"])
            self.assertEqual(forced.exit_code, 0, forced.output)

    def test_split_rejects_rows_without_a_country(self):
        with self.runner.isolated_filesystem():
            write(Path("source.yaml"), "universities:\n  - {domain: mit.edu}\n")
            result = self.runner.invoke(cli, ["split", "source.yaml", "--out", "curated"])
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("2-letter country code", result.output)
            self.assertFalse(Path("curated").exists())

    def test_normalize_defaults_to_imported_then_curated(self):
        with self.runner.isolated_filesystem():
            write(Path("data/sources/imported/us.yaml"),
                  "universities:\n  - {domain: mit.edu, country: US, priority: 3}\n")
            write(Path("data/sources/curated/us.yaml"), "universities:\n  - {domain: mit.edu, priority: 1}\n")
            result = self.runner.invoke(cli, ["normalize"])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("1 override(s)", result.output)
            (mit,) = json.loads(Path("dist/universities.canonical.json").read_text())
            self.assertEqual(mit["priority"], 1)

    def test_normalize_outside_repo_root_explains_itself(self):
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(cli, ["normalize"])
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("not found", result.output)

    def test_import_hipo_command(self):
        with self.runner.isolated_filesystem():
            write(Path("w.json"), json.dumps(HIPO_SAMPLE))
            result = self.runner.invoke(cli, ["import-hipo", "w.json"])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("Imported 3 of 7", result.output)
            self.assertTrue(Path("data/sources/imported/us.yaml").exists())
            self.assertTrue(Path("data/reports/hipo_import.yaml").exists())


if __name__ == "__main__":
    unittest.main()
