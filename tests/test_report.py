import json
import tempfile
import unittest
from pathlib import Path

from scripts.report import compare


class TestReportComparison(unittest.TestCase):
    def test_window_normalized_comparison(self):
        # Setup mock estimated report: 730h monthly delta = +$73.00 (i.e. $0.10/hour)
        estimated_data = {
            "namespace": "team-checkout",
            "environment": "production",
            "delta_usd": 73.0,
            "current_spend_usd": 10.0,
            "budget_usd": 100.0,
            "data_as_of": "2026-10-25T00:00:00Z",
        }
        # In a 24-hour observation window: expected delta = ($73 / 730) * 24 = $2.40
        # If actual spend after 24 hours rose from $10.00 to $12.40 -> delta is $2.40 (0% error)
        actual_data = {
            "environment": "production",
            "period": "2026-10",
            "days_in_month": 31,
            "days_elapsed": 26,
            "days_remaining": 5,
            "data_as_of": "2026-10-26T00:00:00Z",
            "unattributed_pct": 5.0,
            "costs": [
                {
                    "namespace": "team-checkout",
                    "environment": "production",
                    "current_spend_usd": 12.40,
                    "data_as_of": "2026-10-26T00:00:00Z"
                }
            ]
        }

        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as f_est:
            f_est.write(json.dumps(estimated_data))
            p_est = Path(f_est.name)

        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as f_act:
            f_act.write(json.dumps(actual_data))
            p_act = Path(f_act.name)

        try:
            res = compare(str(p_est), str(p_act), override_window_hours=24.0)
            self.assertEqual(res["window_hours"], 24.0)
            self.assertEqual(res["actual_window_delta_usd"], 2.40)
            self.assertAlmostEqual(res["expected_window_delta_usd"], 2.40, places=2)
            self.assertAlmostEqual(res["forecast_error_pct"], 0.0, places=1)
            self.assertEqual(res["forecast_quality"], "EXCELLENT")
            self.assertFalse(res["budget_breach"])
        finally:
            p_est.unlink()
            p_act.unlink()


if __name__ == "__main__":
    unittest.main()
