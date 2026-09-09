from __future__ import annotations

import unittest
import os
from pathlib import Path
from unittest import mock

from streamlit.testing.v1 import AppTest


class AppSmokeTests(unittest.TestCase):
    @property
    def app_path(self):
        return str(Path(__file__).resolve().parents[1] / "app.py")

    def test_shared_password_mode_blocks_then_unlocks(self):
        with mock.patch.dict(
            os.environ,
            {"CRM_AUTH_MODE": "shared_password", "CRM_ACCESS_PASSWORD": "test-password"},
        ):
            app = AppTest.from_file(self.app_path, default_timeout=30).run()
            self.assertEqual(app.title[0].value, "Fundraising CRM")
            self.assertEqual(len(app.sidebar.radio), 0)
            app.text_input[0].set_value("test-password")
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertGreaterEqual(len(app.sidebar.radio), 2)

    def test_every_navigation_page_renders(self):
        pages = {
            "Apply": ["Apply now", "Accelerators & programmes", "Deal room"],
            "Talk": [
                "Who to talk to", "Priority investors", "Investor funnel",
                "Firm workspace", "Conflicts & exclusions",
            ],
            "Track": [
                "Fundraising board", "Round status", "Follow-ups & actions", "Objections",
                "In the news", "Changelog",
            ],
            "Settings": ["Signals & sources", "Scoring", "Data"],
        }
        for section, section_pages in pages.items():
            for page in section_pages:
                app = AppTest.from_file(self.app_path, default_timeout=30).run()
                app.sidebar.radio[0].set_value(section).run()
                app.sidebar.radio[1].set_value(page).run()
                self.assertFalse(app.exception, f"{section} / {page} failed")


if __name__ == "__main__":
    unittest.main()
