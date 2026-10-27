#!/usr/bin/env python3
"""
ingest_costs.py — Fetches current-month AWS spend per namespace and environment via Cost Explorer.

Usage:
    python scripts/ingest_costs.py [namespace1 namespace2 ...] [--environment production]
    python scripts/ingest_costs.py team-checkout team-search --environment production

Output:
    data/costs.json with per-namespace spend, environment, date breakdowns, and staleness timestamps.

NOTE: AWS Cost Explorer data lags ~24 hours and uses UTC calendar days.
"""

import argparse
import calendar
import datetime
import json
import sys
from pathlib import Path

try:
    import boto3
except ImportError:
    boto3 = None

try:
    import yaml
except ImportError:
    yaml = None

# ── Config ────────────────────────────────────────────────────────────────────
TAG_KEY      = "cost-center"         # Kubernetes namespace cost-center tag
ENV_TAG_KEY  = "Environment"         # Environment tag (e.g., production, staging)
ROOT_DIR     = Path(__file__).parent.parent
OUTPUT_FILE  = ROOT_DIR / "data" / "costs.json"
BUDGETS_FILE = ROOT_DIR / "config" / "budgets.yaml"
DEFAULT_NS   = ["team-checkout", "team-search"]
CE_REGION    = "us-east-1"           # Cost Explorer API is always us-east-1


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_month_range(ref_date: datetime.date = None) -> tuple[str, str, str, int, int, int]:
    """Return (start, end, period_str, days_in_month, days_elapsed, days_remaining) using UTC.
    Cost Explorer end date is exclusive, so 'end' is set to ref_date + 1 day."""
    if ref_date is None:
        ref_date = datetime.datetime.now(datetime.timezone.utc).date()

    start = ref_date.replace(day=1).isoformat()
    end   = (ref_date + datetime.timedelta(days=1)).isoformat()
    period = ref_date.strftime("%Y-%m")

    # Calendar days calculation
    _, days_in_month = calendar.monthrange(ref_date.year, ref_date.month)
    days_elapsed = min(ref_date.day, days_in_month)
    days_remaining = max(0, days_in_month - days_elapsed)

    return start, end, period, days_in_month, days_elapsed, days_remaining


def build_ce_filter(namespace: str, environment: str = None) -> dict:
    """Build a tag filter for Cost Explorer combining namespace and environment."""
    tag_filters = [
        {
            "Tags": {
                "Key": TAG_KEY,
                "Values": [namespace],
                "MatchOptions": ["EQUALS"],
            }
        }
    ]
    if environment:
        tag_filters.append(
            {
                "Tags": {
                    "Key": ENV_TAG_KEY,
                    "Values": [environment],
                    "MatchOptions": ["EQUALS"],
                }
            }
        )

    if len(tag_filters) == 1:
        return tag_filters[0]
    return {"And": tag_filters}


def fetch_namespace_spend(client, namespace: str, start: str, end: str, environment: str = None) -> float:
    """Query Cost Explorer for spend attributed to a specific namespace and environment."""
    ce_filter = build_ce_filter(namespace, environment)
    resp = client.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
        Filter=ce_filter,
    )
    results = resp.get("ResultsByTime", [])
    if not results:
        return 0.0
    return float(results[0]["Total"]["UnblendedCost"]["Amount"])


def fetch_total_spend(client, start: str, end: str, environment: str = None) -> float:
    """Query Cost Explorer for total account/environment spend (used for unattributed %)."""
    kwargs = {
        "TimePeriod": {"Start": start, "End": end},
        "Granularity": "MONTHLY",
        "Metrics": ["UnblendedCost"],
    }
    if environment:
        kwargs["Filter"] = {
            "Tags": {
                "Key": ENV_TAG_KEY,
                "Values": [environment],
                "MatchOptions": ["EQUALS"],
            }
        }
    resp = client.get_cost_and_usage(**kwargs)
    results = resp.get("ResultsByTime", [])
    if not results:
        return 0.0
    return float(results[0]["Total"]["UnblendedCost"]["Amount"])


def load_default_environment() -> str:
    if not BUDGETS_FILE.exists():
        return "production"
    if yaml is None:
        raise RuntimeError("pyyaml is required to read config/budgets.yaml")
    try:
        cfg = yaml.safe_load(BUDGETS_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Unable to read {BUDGETS_FILE}: {exc}") from exc
    environment = cfg.get("default_environment")
    if not isinstance(environment, str) or not environment:
        raise ValueError(f"{BUDGETS_FILE} must define a non-empty default_environment")
    return environment


# ── Main ──────────────────────────────────────────────────────────────────────

def ingest(namespaces: list[str], environment: str = "production", as_of_date: str = None) -> dict:
    if boto3 is None:
        raise RuntimeError("boto3 is required for AWS Cost Explorer ingestion.")

    ref_date = datetime.date.fromisoformat(as_of_date) if as_of_date else None
    start, end, period, days_in_month, days_elapsed, days_remaining = get_month_range(ref_date)
    data_as_of = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Fetching AWS Cost Explorer data for period {period} (UTC: {start} → {end})")
    print(f"Environment: {environment}  |  Cost-center tag: {TAG_KEY}")
    print(f"Days in month: {days_in_month}  |  Elapsed: {days_elapsed}  |  Remaining: {days_remaining}")

    client = boto3.client("ce", region_name=CE_REGION)

    namespace_costs = []
    for ns in namespaces:
        try:
            amount = fetch_namespace_spend(client, ns, start, end, environment)
        except Exception as e:
            print(f"ERROR: Failed to fetch spend for {ns}: {e}", file=sys.stderr)
            raise

        namespace_costs.append({
            "namespace":         ns,
            "environment":       environment,
            "current_spend_usd": round(amount, 4),
            "period":            period,
            "data_as_of":        data_as_of,
        })
        print(f"  {ns} ({environment}): ${amount:.4f}")

    total = fetch_total_spend(client, start, end, environment)
    attributed = sum(c["current_spend_usd"] for c in namespace_costs)
    unattributed_pct = round((1 - attributed / total) * 100, 1) if total > 0 else 0.0

    print(f"\nTotal spend ({environment}): ${total:.4f}")
    print(f"Attributed spend:          ${attributed:.4f}")
    print(f"Unattributed:              {unattributed_pct}%")

    output = {
        "environment":         environment,
        "period":              period,
        "billing_period_start": start,
        "billing_period_end":   end,
        "days_in_month":       days_in_month,
        "days_elapsed":        days_elapsed,
        "days_remaining":      days_remaining,
        "data_as_of":          data_as_of,
        "costs":               namespace_costs,
        "total_spend_usd":     round(total, 4),
        "attributed_spend_usd": round(attributed, 4),
        "unattributed_pct":    unattributed_pct,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\n✅ Written to {OUTPUT_FILE}")
    return output


def main():
    default_env = load_default_environment()
    parser = argparse.ArgumentParser(description="Ingest AWS Cost Explorer spend for Kubernetes namespaces")
    parser.add_argument("namespaces", nargs="*", default=DEFAULT_NS, help="Namespaces to query")
    parser.add_argument("--environment", default=default_env, help=f"Environment tag value (default: {default_env})")
    parser.add_argument("--as-of-date", default=None, help="Reference date YYYY-MM-DD for deterministic queries")
    args = parser.parse_args()

    ingest(args.namespaces, environment=args.environment, as_of_date=args.as_of_date)


if __name__ == "__main__":
    main()
