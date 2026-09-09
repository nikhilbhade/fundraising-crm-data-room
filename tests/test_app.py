from __future__ import annotations

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest


class AppSmokeTests(unittest.TestCase):
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
        app_path = str(Path(__file__).resolve().parents[1] / "app.py")
        for section, section_pages in pages.items():
            for page in section_pages:
                app = AppTest.from_file(app_path, default_timeout=30).run()
                app.sidebar.radio[0].set_value(section).run()
                app.sidebar.radio[1].set_value(page).run()
                self.assertFalse(app.exception, f"{section} / {page} failed")


if __name__ == "__main__":
    unittest.main()
