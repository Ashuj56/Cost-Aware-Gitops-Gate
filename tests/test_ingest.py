import datetime
import unittest

from scripts.ingest_costs import get_month_range, build_ce_filter


class TestIngestCosts(unittest.TestCase):
    def test_get_month_range_utc_deterministic(self):
        ref_date = datetime.date(2026, 10, 15)
        start, end, period, days_in_month, elapsed, remaining = get_month_range(ref_date)
        self.assertEqual(start, "2026-10-01")
        self.assertEqual(end, "2026-10-16")
        self.assertEqual(period, "2026-10")
        self.assertEqual(days_in_month, 31)
        self.assertEqual(elapsed, 15)
        self.assertEqual(remaining, 16)

    def test_get_month_range_first_of_month(self):
        ref_date = datetime.date(2026, 2, 1)
        start, end, period, days_in_month, elapsed, remaining = get_month_range(ref_date)
        self.assertEqual(start, "2026-02-01")
        self.assertEqual(end, "2026-02-02")
        self.assertEqual(days_in_month, 28)
        self.assertEqual(elapsed, 1)
        self.assertEqual(remaining, 27)

    def test_build_ce_filter_namespace_only(self):
        ce_filter = build_ce_filter(namespace="team-checkout")
        self.assertIn("Tags", ce_filter)
        self.assertEqual(ce_filter["Tags"]["Key"], "cost-center")
        self.assertEqual(ce_filter["Tags"]["Values"], ["team-checkout"])

    def test_build_ce_filter_with_environment(self):
        ce_filter = build_ce_filter(namespace="team-checkout", environment="production")
        self.assertIn("And", ce_filter)
        self.assertEqual(len(ce_filter["And"]), 2)
        tags = {f["Tags"]["Key"]: f["Tags"]["Values"] for f in ce_filter["And"]}
        self.assertEqual(tags["cost-center"], ["team-checkout"])
        self.assertEqual(tags["Environment"], ["production"])


if __name__ == "__main__":
    unittest.main()
