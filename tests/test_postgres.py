from __future__ import annotations

import os
import unittest

from crm import database as db


@unittest.skipUnless(os.environ.get("TEST_POSTGRES_URL"), "TEST_POSTGRES_URL is not set")
class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_url = os.environ.get("DATABASE_URL")
        cls.previous_engine = os.environ.get("DB_ENGINE")
        os.environ["DATABASE_URL"] = os.environ["TEST_POSTGRES_URL"]
        os.environ.pop("DB_ENGINE", None)
        cls.conn = db.bootstrap()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        if cls.previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = cls.previous_url
        if cls.previous_engine is None:
            os.environ.pop("DB_ENGINE", None)
        else:
            os.environ["DB_ENGINE"] = cls.previous_engine

    def test_bootstrap_and_audited_crud(self):
        self.assertEqual(self.conn.backend, "postgresql")
        self.assertGreaterEqual(
            self.conn.execute("SELECT COUNT(*) FROM firms").fetchone()[0], 100
        )
        db.set_actor("postgres-integration-test")
        record_id = "pc_postgres_integration"
        existing = self.conn.execute(
            "SELECT company_id FROM portfolio_companies WHERE company_id=?", (record_id,)
        ).fetchone()
        if existing:
            db.delete_row(
                self.conn, "portfolio_companies", {"company_id": record_id},
                source="postgres_test_cleanup",
            )
        db.insert_row(
            self.conn,
            "portfolio_companies",
            {"company_id": record_id, "name": "PostgreSQL persistence check"},
            source="postgres_test",
        )
        db.update_row(
            self.conn,
            "portfolio_companies",
            {"company_id": record_id},
            {"notes": "committed"},
            source="postgres_test",
        )
        row = self.conn.execute(
            "SELECT notes FROM portfolio_companies WHERE company_id=?", (record_id,)
        ).fetchone()
        self.assertEqual(row["notes"], "committed")
        audit_count = self.conn.execute(
            "SELECT COUNT(*) FROM change_log WHERE actor=? AND record_key LIKE ?",
            ("postgres-integration-test", f"%{record_id}%"),
        ).fetchone()[0]
        self.assertGreaterEqual(audit_count, 2)
        db.delete_row(
            self.conn, "portfolio_companies", {"company_id": record_id},
            source="postgres_test",
        )


if __name__ == "__main__":
    unittest.main()
