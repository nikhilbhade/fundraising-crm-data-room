from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from crm import database as db


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("FUNDRAISING_CRM_DB")
        os.environ["FUNDRAISING_CRM_DB"] = str(Path(self.tmp.name) / "test.db")
        self.conn = db.bootstrap()

    def tearDown(self):
        self.conn.close()
        if self.previous is None:
            os.environ.pop("FUNDRAISING_CRM_DB", None)
        else:
            os.environ["FUNDRAISING_CRM_DB"] = self.previous
        self.tmp.cleanup()

    def scalar(self, sql: str):
        return self.conn.execute(sql).fetchone()[0]

    def test_seed_contains_full_actionable_universe(self):
        self.assertGreaterEqual(self.scalar("SELECT COUNT(*) FROM firms"), 100)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM firms"), self.scalar("SELECT COUNT(*) FROM fund_vehicles"))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM firms"), self.scalar("SELECT COUNT(*) FROM opportunities"))
        self.assertGreaterEqual(self.scalar("SELECT COUNT(*) FROM programs"), 10)

    def test_apply_decision_and_linkedin_fields_exist(self):
        firm_cols = set(db.table_columns(self.conn, "firms"))
        opp_cols = set(db.table_columns(self.conn, "opportunities"))
        self.assertTrue({"access_mode", "application_url", "decision_process", "target_partner_role", "linkedin_query"} <= firm_cols)
        self.assertTrue({"application_status", "application_deadline", "decision_status", "decision_next_gate"} <= opp_cols)
        blank_roles = self.scalar("SELECT COUNT(*) FROM firms WHERE target_partner_role IS NULL OR target_partner_role = ''")
        self.assertEqual(blank_roles, 0)

    def test_board_is_seeded_with_real_work(self):
        columns = set(db.table_columns(self.conn, "tasks"))
        self.assertTrue({"workstream", "board_status", "acceptance_criteria", "blocked_by"} <= columns)
        self.assertGreaterEqual(self.scalar("SELECT COUNT(*) FROM tasks"), 15)
        self.assertGreaterEqual(self.scalar("SELECT COUNT(DISTINCT workstream) FROM tasks"), 4)
        self.assertGreaterEqual(self.scalar("SELECT COUNT(DISTINCT board_status) FROM tasks"), 4)

    def test_requested_competitors_are_blocking(self):
        rows = self.conn.execute(
            "SELECT name, severity FROM competitors WHERE name IN ('Loop AI', 'Voosh AI', 'Superorder')"
        ).fetchall()
        self.assertEqual({row["name"] for row in rows}, {"Loop AI", "Voosh AI", "Superorder"})
        self.assertTrue(all(row["severity"] == "Blocking" for row in rows))

    def test_priority_contacts_are_named_and_source_backed(self):
        rows = self.conn.execute(
            """SELECT full_name, linkedin_url, source_url, verification_status
               FROM contacts"""
        ).fetchall()
        self.assertGreaterEqual(len(rows), 10)
        self.assertTrue(all(row["full_name"] for row in rows))
        self.assertTrue(all("linkedin.com/search/results/people" in row["linkedin_url"] for row in rows))
        self.assertTrue(all(row["source_url"].startswith("https://") for row in rows))
        self.assertTrue(all(row["verification_status"] == "VERIFIED" for row in rows))

    def test_confirmed_blocking_portfolio_sets_do_not_engage(self):
        db.insert_row(self.conn, "portfolio_companies", {
            "company_id": "pc_loop_test", "name": "Loop AI", "is_competitor": 1,
        })
        db.insert_row(self.conn, "firm_portfolio", {
            "firm_id": "f_menlo", "company_id": "pc_loop_test",
            "relevance": "DIRECT_CONFLICT", "source_url": "https://example.com/evidence",
            "verification_status": "VERIFIED",
        })
        db.recompute_conflicts(self.conn)
        status = self.conn.execute(
            "SELECT engagement_status FROM firms WHERE firm_id = 'f_menlo'"
        ).fetchone()[0]
        self.assertEqual(status, "Do Not Engage")

    def test_partial_csv_upsert_preserves_other_fields(self):
        before = self.conn.execute("SELECT website FROM firms WHERE firm_id='f_menlo'").fetchone()[0]
        count = db.upsert_dataframe(
            self.conn, "firms", pd.DataFrame([{"firm_id": "f_menlo", "access_mode": "Warm intro"}])
        )
        after = self.conn.execute(
            "SELECT website, access_mode FROM firms WHERE firm_id='f_menlo'"
        ).fetchone()
        self.assertEqual(count, 1)
        self.assertEqual(after["website"], before)
        self.assertEqual(after["access_mode"], "Warm intro")

    def test_seed_upgrade_adds_rows_without_overwriting_user_edits(self):
        self.conn.execute(
            "UPDATE firms SET website='https://founder-edited.example', access_mode='' "
            "WHERE firm_id='f_menlo'"
        )
        self.conn.execute("DELETE FROM contacts WHERE contact_id='c_menlo_murphy'")
        self.conn.commit()

        db.merge_seed_defaults(self.conn)

        firm = self.conn.execute(
            "SELECT website, access_mode FROM firms WHERE firm_id='f_menlo'"
        ).fetchone()
        contact = self.conn.execute(
            "SELECT full_name FROM contacts WHERE contact_id='c_menlo_murphy'"
        ).fetchone()
        self.assertEqual(firm["website"], "https://founder-edited.example")
        self.assertTrue(firm["access_mode"])
        self.assertEqual(contact["full_name"], "Matt Murphy")


if __name__ == "__main__":
    unittest.main()
