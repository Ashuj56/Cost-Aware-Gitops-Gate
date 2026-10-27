import datetime
import unittest
from pathlib import Path
import tempfile
import json
import yaml

from scripts.estimate_cost import (
    parse_cpu,
    parse_memory_gib,
    extract_resources,
    calculate_workload_monthly_cost,
    compute_forecasts,
    verify_cost_data,
)


class TestUnitParsers(unittest.TestCase):
    def test_parse_cpu_valid(self):
        self.assertEqual(parse_cpu("250m"), 0.25)
        self.assertEqual(parse_cpu("1000m"), 1.0)
        self.assertEqual(parse_cpu("2"), 2.0)
        self.assertEqual(parse_cpu(0.5), 0.5)
        self.assertEqual(parse_cpu(1), 1.0)

    def test_parse_cpu_reject_negative(self):
        with self.assertRaises(ValueError):
            parse_cpu("-250m")
        with self.assertRaises(ValueError):
            parse_cpu(-1)

    def test_parse_cpu_reject_invalid(self):
        with self.assertRaises(ValueError):
            parse_cpu("invalid")
        with self.assertRaises(ValueError):
            parse_cpu("")
        with self.assertRaises(ValueError):
            parse_cpu(None)

    def test_parse_memory_gib_valid(self):
        self.assertEqual(parse_memory_gib("256Mi"), 0.25)
        self.assertEqual(parse_memory_gib("1Gi"), 1.0)
        self.assertEqual(parse_memory_gib("1024Mi"), 1.0)
        self.assertAlmostEqual(parse_memory_gib("512Ki"), 0.000488, places=4)
        self.assertEqual(parse_memory_gib("1073741824"), 1.0)  # Plain bytes

    def test_parse_memory_gib_reject_negative(self):
        with self.assertRaises(ValueError):
            parse_memory_gib("-1Gi")

    def test_parse_memory_gib_reject_invalid(self):
        with self.assertRaises(ValueError):
            parse_memory_gib("512MB")  # Invalid unit
        with self.assertRaises(ValueError):
            parse_memory_gib("abc")


class TestManifestExtraction(unittest.TestCase):
    def test_extract_valid_deployment(self):
        manifest = {
            "kind": "Deployment",
            "metadata": {"name": "test-deploy", "namespace": "team-checkout"},
            "spec": {
                "replicas": 3,
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "app",
                                "resources": {
                                    "requests": {"cpu": "500m", "memory": "512Mi"}
                                }
                            }
                        ]
                    }
                }
            }
        }
        res = extract_resources(manifest, expected_namespace="team-checkout")
        self.assertEqual(res["replicas"], 3)
        self.assertEqual(res["cpu_cores"], 0.5)
        self.assertEqual(res["memory_gib"], 0.5)

    def test_extract_missing_requests_fallback_to_limits(self):
        manifest = {
            "kind": "Deployment",
            "spec": {
                "replicas": 2,
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "app",
                                "resources": {
                                    "limits": {"cpu": "250m", "memory": "256Mi"}
                                }
                            }
                        ]
                    }
                }
            }
        }
        res = extract_resources(manifest)
        self.assertEqual(res["cpu_cores"], 0.25)
        self.assertEqual(res["memory_gib"], 0.25)

    def test_extract_reject_negative_replicas(self):
        manifest = {
            "kind": "Deployment",
            "spec": {
                "replicas": -1,
                "template": {"spec": {"containers": [{"name": "app", "resources": {"requests": {"cpu": "1", "memory": "1Gi"}}}]}}
            }
        }
        with self.assertRaises(ValueError):
            extract_resources(manifest)

    def test_extract_reject_wrong_kind(self):
        manifest = {"kind": "StatefulSet", "spec": {}}
        with self.assertRaises(ValueError):
            extract_resources(manifest)

    def test_extract_reject_namespace_mismatch(self):
        manifest = {
            "kind": "Deployment",
            "metadata": {"namespace": "team-search"},
            "spec": {
                "replicas": 1,
                "template": {"spec": {"containers": [{"name": "app", "resources": {"requests": {"cpu": "1", "memory": "1Gi"}}}]}}
            }
        }
        with self.assertRaises(ValueError):
            extract_resources(manifest, expected_namespace="team-checkout")


class TestFinOpsForecast(unittest.TestCase):
    def test_forecast_eom_and_run_rate(self):
        # 31 day month, 15 days elapsed, 16 remaining
        # MTD spend is $15 ($1/day baseline)
        # Old workload cost: $30/mo ($1/day)
        # New workload cost: $60/mo ($2/day) -> delta is +$30/mo
        fc = compute_forecasts(
            current_spend_mtd=15.0,
            before_cost=30.0,
            after_cost=60.0,
            days_in_month=31,
            days_elapsed=15,
            days_remaining=16,
        )
        self.assertEqual(fc["delta_monthly_usd"], 30.0)
        # delta_eom = 30 * (16 / 31) ≈ 15.4839
        self.assertAlmostEqual(fc["delta_eom_usd"], 15.4839, places=2)
        # extrapolated baseline: 15 / 15 * 31 = 31.0
        # projected_eom = 15 + (1 * 16) + 15.4839 = 46.4839
        self.assertAlmostEqual(fc["projected_eom_spend_usd"], 46.4839, places=2)
        # projected monthly run rate = 31 + 30 = 61.0
        self.assertAlmostEqual(fc["projected_monthly_run_rate_usd"], 61.0, places=1)


class TestCostDataHealth(unittest.TestCase):
    def test_fail_closed_on_missing_file(self):
        missing_path = Path("/nonexistent/costs.json")
        data_avail, is_stale, _, _, _, _ = verify_cost_data(missing_path, "team-checkout")
        self.assertFalse(data_avail)
        self.assertTrue(is_stale)

    def test_detect_stale_data(self):
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as f:
            # 72 hours ago
            stale_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
            data = {
                "data_as_of": stale_time,
                "costs": [{
                    "namespace": "team-checkout",
                    "environment": "production",
                    "current_spend_usd": 10.0,
                    "data_as_of": stale_time,
                }]
            }
            f.write(json.dumps(data))
            f_path = Path(f.name)

        try:
            data_avail, is_stale, age, _, _, _ = verify_cost_data(f_path, "team-checkout", max_age_hours=48.0)
            self.assertTrue(data_avail)
            self.assertTrue(is_stale)
            self.assertGreater(age, 48.0)
        finally:
            f_path.unlink()


if __name__ == "__main__":
    unittest.main()
