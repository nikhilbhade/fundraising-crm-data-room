from __future__ import annotations

import unittest

from crm import scoring


class ScoringTests(unittest.TestCase):
    def test_seed_stage_ai_firm_scores_above_growth_mismatch(self):
        weights = scoring.default_weights()
        tag_weights = {"t_ai_b2b_saas": 10.0, "t_first_institutional": 4.0}
        seed = scoring.score_firm(
            {
                "stage_focus": "Pre-seed,Seed", "geo_segment": "US", "leads_rounds": 1,
                "check_min_usd_k": 250, "check_max_usd_k": 2000,
            },
            weights=weights, tag_weights=tag_weights,
            firm_tags={"t_ai_b2b_saas": 0.9, "t_first_institutional": 0.9},
            fund_row={"vintage_year": 2026},
        )
        growth = scoring.score_firm(
            {
                "stage_focus": "Growth", "geo_segment": "OTHER", "leads_rounds": 0,
                "check_min_usd_k": 10000, "check_max_usd_k": 50000,
            },
            weights=weights, tag_weights=tag_weights,
            firm_tags={"t_ai_b2b_saas": 0.5}, fund_row={"vintage_year": 2020},
        )
        self.assertGreater(seed["score"], growth["score"])

    def test_direct_conflict_applies_penalty_and_flag(self):
        result = scoring.score_firm(
            {"stage_focus": "Seed", "geo_segment": "US", "leads_rounds": 1},
            weights=scoring.default_weights(), tag_weights={"ai": 10},
            firm_tags={"ai": 1},
            portfolio_rows=[{"relevance": "DIRECT_CONFLICT", "company_name": "Loop AI"}],
            fund_row={"vintage_year": 2026},
        )
        self.assertIn("penalty_direct_conflict", result["penalties"])
        self.assertTrue(any("Loop AI" in flag for flag in result["flags"]))


if __name__ == "__main__":
    unittest.main()
