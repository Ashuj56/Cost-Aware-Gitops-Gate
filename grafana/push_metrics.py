#!/usr/bin/env python3
"""
push_metrics.py — Expose reliable cost gate FinOps metrics in Prometheus text format.
Exposes MTD actuals, End-of-Month (EOM) forecast, steady-state run-rate, and data freshness.

Usage:
    python grafana/push_metrics.py

Output:
    Prometheus exposition text format to stdout.
    If PUSHGATEWAY_URL env var is set, pushes to Pushgateway.
"""

import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT        = Path(__file__).parent.parent
COSTS_FILE  = ROOT / "data" / "costs.json"
REPORT_FILE = ROOT / "data" / "cost_report.json"


def generate_metrics() -> str:
    lines = []

    # ── costs.json metrics ────────────────────────────────────────────────────
    if COSTS_FILE.exists():
        try:
            data = json.loads(COSTS_FILE.read_text())
            env  = data.get("environment", "production")

            lines.append("# HELP cost_gate_current_spend_mtd_usd Month-to-date actual spend from AWS Cost Explorer")
            lines.append("# TYPE cost_gate_current_spend_mtd_usd gauge")
            for ns in data.get("costs", []):
                ns_name = ns.get("namespace", "unknown")
                lines.append(
                    f'cost_gate_current_spend_mtd_usd{{namespace="{ns_name}",environment="{env}",period="{ns.get("period", "")}"}} '
                    f'{ns.get("current_spend_usd", 0.0)}'
                )

            lines.append("# HELP cost_gate_unattributed_pct Percentage of spend not attributed to a namespace")
            lines.append("# TYPE cost_gate_unattributed_pct gauge")
            lines.append(f'cost_gate_unattributed_pct{{environment="{env}"}} {data.get("unattributed_pct", 0.0)}')

            lines.append("# HELP cost_gate_total_spend_usd Total account spend this billing period")
            lines.append("# TYPE cost_gate_total_spend_usd gauge")
            lines.append(f'cost_gate_total_spend_usd{{environment="{env}"}} {data.get("total_spend_usd", 0.0)}')
        except Exception as e:
            print(f"Warning: could not parse costs.json: {e}", file=sys.stderr)

    # ── cost_report.json metrics ─────────────────────────────────────────────
    if REPORT_FILE.exists():
        try:
            report = json.loads(REPORT_FILE.read_text())
            ns  = report.get("namespace", "unknown")
            env = report.get("environment", "production")

            lines.append("# HELP cost_gate_projected_eom_spend_usd Projected Month-End spend (MTD + prorated delta)")
            lines.append("# TYPE cost_gate_projected_eom_spend_usd gauge")
            lines.append(f'cost_gate_projected_eom_spend_usd{{namespace="{ns}",environment="{env}"}} {report.get("projected_eom_spend_usd", 0.0)}')

            lines.append("# HELP cost_gate_projected_monthly_run_rate_usd Steady-state monthly ongoing spend after deployment")
            lines.append("# TYPE cost_gate_projected_monthly_run_rate_usd gauge")
            lines.append(f'cost_gate_projected_monthly_run_rate_usd{{namespace="{ns}",environment="{env}"}} {report.get("projected_monthly_run_rate_usd", 0.0)}')

            lines.append("# HELP cost_gate_budget_usd Configured monthly budget")
            lines.append("# TYPE cost_gate_budget_usd gauge")
            lines.append(f'cost_gate_budget_usd{{namespace="{ns}",environment="{env}"}} {report.get("budget_usd", 0.0)}')

            lines.append("# HELP cost_gate_alert_threshold_pct Configured alert threshold percentage")
            lines.append("# TYPE cost_gate_alert_threshold_pct gauge")
            lines.append(f'cost_gate_alert_threshold_pct{{namespace="{ns}",environment="{env}"}} {report.get("alert_threshold_pct", 80.0)}')

            lines.append("# HELP cost_gate_budget_utilization_pct Current budget utilization percentage")
            lines.append("# TYPE cost_gate_budget_utilization_pct gauge")
            lines.append(f'cost_gate_budget_utilization_pct{{namespace="{ns}",environment="{env}"}} {report.get("budget_utilization_pct", 0.0)}')

            lines.append("# HELP cost_gate_delta_monthly_usd Estimated monthly workload delta")
            lines.append("# TYPE cost_gate_delta_monthly_usd gauge")
            lines.append(f'cost_gate_delta_monthly_usd{{namespace="{ns}",environment="{env}"}} {report.get("delta_usd", 0.0)}')

            lines.append("# HELP cost_gate_data_freshness_hours Age of underlying Cost Explorer data in hours")
            lines.append("# TYPE cost_gate_data_freshness_hours gauge")
            lines.append(f'cost_gate_data_freshness_hours{{namespace="{ns}",environment="{env}"}} {report.get("data_age_hours", 0.0)}')

            lines.append("# HELP cost_gate_data_stale_flag 1 if cost data is stale (fail closed), 0 if fresh")
            lines.append("# TYPE cost_gate_data_stale_flag gauge")
            stale_val = 1 if report.get("cost_data_stale", False) else 0
            lines.append(f'cost_gate_data_stale_flag{{namespace="{ns}",environment="{env}"}} {stale_val}')
        except Exception as e:
            print(f"Warning: could not parse cost_report.json: {e}", file=sys.stderr)

    return "\n".join(lines)


def push_to_gateway(metrics: str, url: str, job: str = "cost-gate") -> None:
    import urllib.request
    data = metrics.encode("utf-8")
    req = urllib.request.Request(
        f"{url}/metrics/job/{job}",
        data=data,
        method="PUT",
        headers={"Content-Type": "text/plain; version=0.0.4"},
    )
    with urllib.request.urlopen(req) as resp:
        print(f"Pushed to Pushgateway: {resp.status}")


if __name__ == "__main__":
    metrics = generate_metrics()
    print(metrics)

    gateway_url = os.environ.get("PUSHGATEWAY_URL")
    if gateway_url:
        push_to_gateway(metrics, gateway_url)
        print(f"✅ Metrics pushed to {gateway_url}")
    else:
        print("\n(Set PUSHGATEWAY_URL env var to push to a Prometheus Pushgateway)", file=sys.stderr)
