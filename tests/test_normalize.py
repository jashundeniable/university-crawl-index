import json
import tempfile
import unittest
from pathlib import Path

from uni_index.normalize import normalize_file


class TestNormalize(unittest.TestCase):
    def test_valid_yaml_is_cleaned_and_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "source.yaml"
            source.write_text(
                """
universities:
  - domain: Example.EDU
    country: us
    type: public
    priority: 2
    subdomains: ["Admissions.Example.edu", "admissions.example.edu"]
"""
            )
            out = tmp / "canonical.json"

            records, errors = normalize_file(source, out)

            self.assertEqual(errors, [])
            self.assertEqual(len(records), 1)
            rec = records[0]
            self.assertEqual(rec["domain"], "example.edu")       # lowercased
            self.assertEqual(rec["country"], "US")                # uppercased
            self.assertEqual(rec["subdomains"], ["admissions.example.edu"])  # deduped+lowercased
            self.assertIn("source_hash", rec)
            self.assertTrue(out.exists())
            self.assertEqual(json.loads(out.read_text()), records)

    def test_rejects_invalid_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "source.yaml"
            source.write_text(
                """
universities:
  - domain: "not a domain"
    country: US
    type: public
    priority: 1
"""
            )
            out = tmp / "canonical.json"
            records, errors = normalize_file(source, out)

            self.assertEqual(records, [])
            self.assertEqual(len(errors), 1)
            self.assertIn("not a domain", errors[0])

    def test_rejects_subdomain_not_matching_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "source.yaml"
            source.write_text(
                """
universities:
  - domain: mit.edu
    country: US
    type: private
    priority: 1
    subdomains: ["admissions.harvard.edu"]
"""
            )
            out = tmp / "canonical.json"
            records, errors = normalize_file(source, out)

            self.assertEqual(records, [])
            self.assertEqual(len(errors), 1)
            self.assertIn("must end with the university domain", errors[0])

    def test_duplicate_domain_in_same_file_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "source.yaml"
            source.write_text(
                """
universities:
  - domain: mit.edu
    country: US
    type: private
    priority: 1
  - domain: mit.edu
    country: US
    type: private
    priority: 2
"""
            )
            out = tmp / "canonical.json"
            records, errors = normalize_file(source, out)

            self.assertEqual(len(records), 1)
            self.assertEqual(len(errors), 1)
            self.assertIn("duplicate domain", errors[0])

    def test_csv_source_with_pipe_delimited_subdomains(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "source.csv"
            source.write_text(
                "domain,country,type,priority,subdomains\n"
                "mit.edu,US,private,1,admissions.mit.edu|catalog.mit.edu\n"
            )
            out = tmp / "canonical.json"
            records, errors = normalize_file(source, out)

            self.assertEqual(errors, [])
            self.assertEqual(len(records), 1)
            self.assertEqual(
                records[0]["subdomains"], ["admissions.mit.edu", "catalog.mit.edu"]
            )

    def test_output_is_sorted_and_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "source.yaml"
            source.write_text(
                """
universities:
  - domain: zzz-university.edu
    country: US
    priority: 3
  - domain: aaa-university.edu
    country: US
    priority: 3
"""
            )
            out = tmp / "canonical.json"
            records, _ = normalize_file(source, out)

            self.assertEqual(
                [r["domain"] for r in records],
                ["aaa-university.edu", "zzz-university.edu"],
            )


if __name__ == "__main__":
    unittest.main()
