#!/usr/bin/env python3
"""
report.py — Phase 8: Compare estimated cost delta with actual AWS cost after
billing data becomes available (~24-48h after deployment).

Corrects the comparison window by normalizing the actual delta over the
observation window against the 730-hour monthly estimation model.

Usage:
    python scripts/report.py \\
        --estimated data/cost_report.json \\
        --actual    data/costs_after.json \\
        [--window-hours 48.0]
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT        = Path(__file__).parent.parent
DATA_DIR    = ROOT / "data"
OUTPUT_FILE = DATA_DIR / "actuals_comparison.json"
HOURS_PER_MONTH = 730.0


def load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")
    value = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def parse_timestamp(ts_str: str, field_name: str) -> datetime.datetime:
    if not isinstance(ts_str, str) or not ts_str or ts_str.lower() in {
        "unknown", "missing", "corrupt"
    }:
        raise ValueError(f"{field_name} is missing or invalid")
    try:
        value = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid ISO-8601 timestamp: {ts_str!r}") from exc
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value


def compare(estimated_path: str, actual_path: str, override_window_hours: float = None) -> dict:
    estimated = load_json(estimated_path)
    actual    = load_json(actual_path)

    ns = estimated.get("namespace")
    if not isinstance(ns, str) or not ns:
        raise ValueError("Estimated report has no valid namespace")

    estimated_environment = estimated.get("environment")
    actual_environment = actual.get("environment")
    if estimated_environment != actual_environment:
        raise ValueError(
            f"Environment mismatch: estimated={estimated_environment!r}, "
            f"actual={actual_environment!r}"
        )
    if estimated.get("period") and actual.get("period") and estimated["period"] != actual["period"]:
        raise ValueError(
            f"Billing period mismatch: estimated={estimated['period']!r}, "
            f"actual={actual['period']!r}"
        )

    actual_namespace = next(
        (
            c for c in actual.get("costs", [])
            if c.get("namespace") == ns
            and c.get("environment") == estimated_environment
        ),
        None,
    )
    if not actual_namespace:
        raise ValueError(f"Namespace '{ns}' not found in actual costs file: {actual_path}")

    try:
        actual_spend_after = float(actual_namespace["current_spend_usd"])
        actual_spend_before = float(estimated["current_spend_usd"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Spend values must be numeric in both reports") from exc
    if actual_spend_after < 0 or actual_spend_before < 0:
        raise ValueError("Spend values cannot be negative")
    actual_window_delta = round(actual_spend_after - actual_spend_before, 4)

    # Observation window calculation
    if override_window_hours is not None:
        window_hours = float(override_window_hours)
        if window_hours <= 0:
            raise ValueError("--window-hours must be greater than zero")
    else:
        t_before = parse_timestamp(estimated.get("data_as_of"), "estimated.data_as_of")
        t_after = parse_timestamp(
            actual.get("data_as_of") or actual_namespace.get("data_as_of"),
            "actual.data_as_of",
        )
        if t_after <= t_before:
            raise ValueError("Actual cost snapshot must be newer than estimated snapshot")
        elapsed_secs = (t_after - t_before).total_seconds()
        window_hours = round(elapsed_secs / 3600.0, 2)
        if window_hours <= 0:
            raise ValueError("Observation window must be greater than zero")

    try:
        monthly_estimated_delta = float(estimated["delta_usd"])
        budget_usd = float(estimated["budget_usd"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Estimated delta and budget must be numeric") from exc
    if budget_usd <= 0:
        raise ValueError("Estimated budget must be positive")

    # Time-window normalized comparison (Apples-to-Apples)
    expected_window_delta = round((monthly_estimated_delta / HOURS_PER_MONTH) * window_hours, 4)
    actual_monthly_extrapolated_delta = round((actual_window_delta / window_hours) * HOURS_PER_MONTH, 4) if window_hours > 0 else 0.0

    abs_window_error = round(abs(actual_window_delta - expected_window_delta), 4)
    pct_window_error = (
        round(abs_window_error / abs(expected_window_delta) * 100, 1)
        if expected_window_delta != 0
        else 0.0
    )

    days_elapsed = actual.get("days_elapsed")
    days_remaining = actual.get("days_remaining")
    days_in_month = actual.get("days_in_month")
    if (
        isinstance(days_elapsed, int)
        and isinstance(days_remaining, int)
        and isinstance(days_in_month, int)
        and days_elapsed > 0
        and days_in_month > 0
        and days_remaining >= 0
    ):
        actual_monthly_run_rate = round(
            actual_spend_after
            + (actual_spend_after / days_elapsed * days_remaining),
            4,
        )
    else:
        actual_monthly_run_rate = actual_spend_after
    budget_breach = actual_monthly_run_rate > budget_usd
    try:
        attribution_pct = round(100.0 - float(actual["unattributed_pct"]), 1)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Actual report must contain numeric unattributed_pct") from exc

    quality = "EXCELLENT" if pct_window_error <= 10 else ("GOOD" if pct_window_error <= 25 else "POOR")

    result = {
        "namespace":                        ns,
        "environment":                      estimated.get("environment", "production"),
        "window_hours":                     window_hours,
        "monthly_budget_usd":               budget_usd,
        "spend_before_deployment_usd":      actual_spend_before,
        "spend_after_deployment_usd":       actual_spend_after,
        "actual_window_delta_usd":          actual_window_delta,
        "expected_window_delta_usd":        expected_window_delta,
        "monthly_estimated_delta_usd":      monthly_estimated_delta,
        "actual_monthly_extrapolated_usd":  actual_monthly_extrapolated_delta,
        "actual_monthly_run_rate_usd":      actual_monthly_run_rate,
        "absolute_window_error_usd":        abs_window_error,
        "forecast_error_pct":               pct_window_error,
        "forecast_quality":                 quality,
        "budget_breach":                    budget_breach,
        "attribution_coverage_pct":         attribution_pct,
        "estimated_data_as_of":             estimated.get("data_as_of", "unknown"),
        "actual_data_as_of":                actual.get("data_as_of", "unknown"),
        "report_generated_at":              datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║         FORECAST ACCURACY REPORT — {ns:<24} ║
╠══════════════════════════════════════════════════════════════╣
║  Observation window:        {window_hours:<6.1f} hours                     ║
║  Monthly budget:            ${budget_usd:<8.2f}/month               ║
║  Spend before deployment:   ${actual_spend_before:<8.4f}                       ║
║  Spend after deployment:    ${actual_spend_after:<8.4f}                       ║
╠══════════════════════════════════════════════════════════════╣
║  Window Comparison (Normalized for {window_hours:.1f}h)                  ║
║   Expected delta in window: ${expected_window_delta:<+8.4f}                       ║
║   Actual delta in window:   ${actual_window_delta:<+8.4f}                       ║
║   Window absolute error:    ${abs_window_error:<8.4f}                        ║
║   Forecast error:           {pct_window_error:<6.1f}%                        ║
║   Forecast quality:         {quality:<36} ║
╠══════════════════════════════════════════════════════════════╣
║  Monthly Run-Rate View (Extrapolated)                        ║
║   Estimated monthly delta:  ${monthly_estimated_delta:<+8.2f}/month               ║
║   Actual extrapolated delta:${actual_monthly_extrapolated_delta:<+8.2f}/month               ║
╠══════════════════════════════════════════════════════════════╣
║  Budget breach:             {'YES ⚠️ ' if budget_breach else 'NO  ✅':<36} ║
║  Attribution coverage:      {attribution_pct:<6.1f}%                        ║
║  Actual data as of:         {result['actual_data_as_of']:<36} ║
╚══════════════════════════════════════════════════════════════╝
""")
    print(f"✅ Accuracy report written to {OUTPUT_FILE}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Compare estimated vs actual deployment cost with time-window normalization")
    parser.add_argument("--estimated",    required=True, help="Path to data/cost_report.json")
    parser.add_argument("--actual",       required=True, help="Path to actual costs.json after deployment")
    parser.add_argument("--window-hours", type=float, default=None, help="Explicit elapsed hours between snapshots")
    args = parser.parse_args()

    compare(args.estimated, args.actual, override_window_hours=args.window_hours)


if __name__ == "__main__":
    main()
