import tempfile
import unittest
from pathlib import Path

from uni_index.db import connect, init_db, mark_status, status_counts, sync_records


class TestSyncAndDedupe(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")

    def tearDown(self):
        self.tmpdir.cleanup()

    @staticmethod
    def _record(**overrides):
        base = {
            "domain": "example.edu",
            "country": "US",
            "type": "public",
            "subdomains": ["a.example.edu"],
            "priority": 2,
            "source_hash": "hash1",
        }
        base.update(overrides)
        return base

    def test_insert_then_unchanged_then_update(self):
        rec = self._record()

        with connect(self.db_path) as conn:
            init_db(conn)
            stats = sync_records(conn, [rec])
        self.assertEqual(stats, {"inserted": 1, "updated": 0, "unchanged": 0})

        # Re-syncing the exact same record should touch nothing
        with connect(self.db_path) as conn:
            stats2 = sync_records(conn, [rec])
        self.assertEqual(stats2, {"inserted": 0, "updated": 0, "unchanged": 1})

        # A changed hash should update fields AND reset crawl_status to pending
        changed = self._record(priority=1, source_hash="hash2")
        with connect(self.db_path) as conn:
            stats3 = sync_records(conn, [changed])
        self.assertEqual(stats3, {"inserted": 0, "updated": 1, "unchanged": 0})

        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT priority, crawl_status FROM universities WHERE domain=?",
                ("example.edu",),
            ).fetchone()
        self.assertEqual(row["priority"], 1)
        self.assertEqual(row["crawl_status"], "pending")

    def test_second_domain_is_a_separate_row(self):
        with connect(self.db_path) as conn:
            init_db(conn)
            stats = sync_records(
                conn, [self._record(), self._record(domain="other.edu", source_hash="hash9")]
            )
        self.assertEqual(stats["inserted"], 2)

    def test_mark_status_updates_existing_domain(self):
        with connect(self.db_path) as conn:
            init_db(conn)
            sync_records(conn, [self._record()])

        with connect(self.db_path) as conn:
            found = mark_status(conn, "example.edu", "done")
        self.assertTrue(found)

        with connect(self.db_path) as conn:
            counts = status_counts(conn)
        self.assertEqual(counts.get("done"), 1)

    def test_mark_status_rejects_unknown_status(self):
        with connect(self.db_path) as conn:
            init_db(conn)
            sync_records(conn, [self._record()])
            with self.assertRaises(ValueError):
                mark_status(conn, "example.edu", "not-a-real-status")

    def test_mark_status_missing_domain_returns_false(self):
        with connect(self.db_path) as conn:
            init_db(conn)
            found = mark_status(conn, "nope.edu", "done")
        self.assertFalse(found)


if __name__ == "__main__":
    unittest.main()
