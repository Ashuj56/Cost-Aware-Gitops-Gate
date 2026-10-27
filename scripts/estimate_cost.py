#!/usr/bin/env python3
"""
estimate_cost.py — Diffs two Kubernetes Deployment manifests and estimates
cost impact using configured CPU/memory pricing rates and Cost Explorer data.

Provides two mathematically distinct FinOps models:
1. End-of-Month (EOM) Forecast: MTD actuals + prorated delta for remaining days.
2. Steady-State Monthly Run-Rate: Full monthly ongoing cost after deployment.

Fails closed when:
- Cost data is missing or namespace is unattributed.
- Cost data is older than configured staleness threshold.
- Manifest syntax or resource requests are invalid/missing.
"""

import argparse
import calendar
import datetime
import json
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT         = Path(__file__).parent.parent
DATA_DIR     = ROOT / "data"
PRICING_FILE = ROOT / "config" / "pricing.yaml"
BUDGETS_FILE = ROOT / "config" / "budgets.yaml"
COSTS_FILE   = DATA_DIR / "costs.json"
REPORT_FILE  = DATA_DIR / "cost_report.json"


# ── Unit Parsers & Validation ─────────────────────────────────────────────────

def parse_cpu(val) -> float:
    """Convert Kubernetes CPU strings/numbers to fractional cores.
    Valid: '250m' → 0.25, '2' → 2.0, 0.5 → 0.5
    Rejects negative values, empty, or non-numeric.
    """
    if val is None:
        raise ValueError("CPU value cannot be None")
    s = str(val).strip()
    if not s:
        raise ValueError("CPU value cannot be empty")

    if s.endswith("m"):
        num_str = s[:-1]
        try:
            num = float(num_str)
        except ValueError:
            raise ValueError(f"Invalid millicore CPU quantity: {val!r}")
        if num < 0:
            raise ValueError(f"CPU cannot be negative: {val!r}")
        return round(num / 1000.0, 4)

    try:
        num = float(s)
    except ValueError:
        raise ValueError(f"Invalid CPU quantity: {val!r}")

    if num < 0:
        raise ValueError(f"CPU cannot be negative: {val!r}")
    return round(num, 4)


def parse_memory_gib(val) -> float:
    """Convert Kubernetes memory quantities to GiB.
    Valid units: Ki, Mi, Gi, Ti (binary) and K, M, G, T (decimal).
    Rejects negative values or invalid units.
    """
    if val is None:
        raise ValueError("Memory value cannot be None")
    s = str(val).strip()
    if not s:
        raise ValueError("Memory value cannot be empty")

    m = re.match(r"^([+-]?\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti|K|M|G|T)?$", s)
    if not m:
        raise ValueError(f"Invalid Kubernetes memory quantity: {val!r}. Must use units like Mi, Gi, etc.")

    num = float(m.group(1))
    if num < 0:
        raise ValueError(f"Memory cannot be negative: {val!r}")

    unit = m.group(2) or ""
    factors = {
        "Ki": 1.0 / (1024 ** 2),
        "Mi": 1.0 / 1024,
        "Gi": 1.0,
        "Ti": 1024.0,
        "K":  1.0 / (1000 ** 2),   # Kubernetes decimal SI representation
        "M":  1.0 / 1000,
        "G":  1.0 * (1000 ** 3) / (1024 ** 3),
        "T":  1.0 * (1000 ** 4) / (1024 ** 3),
        "":   1.0 / (1024 ** 3),   # Plain integer bytes to GiB
    }
    return round(num * factors[unit], 4)


def extract_resources(manifest: Optional[dict], expected_namespace: str = None) -> dict:
    """Extract replica count, CPU, and memory requests from a Deployment manifest.
    Validates manifest kind, namespace alignment, non-negative replicas, and requests.
    Falls back to container limits if requests are omitted (Kubernetes standard behavior).
    """
    if manifest is None:
        return {"replicas": 0, "cpu_cores": 0.0, "memory_gib": 0.0, "containers": []}

    if not isinstance(manifest, dict):
        raise ValueError(f"Manifest must be a YAML dictionary, got {type(manifest).__name__}")

    kind = manifest.get("kind")
    if kind != "Deployment":
        raise ValueError(f"Unsupported manifest kind '{kind}'. Expected 'Deployment'")

    # Optional namespace verification
    manifest_ns = manifest.get("metadata", {}).get("namespace")
    if manifest_ns and expected_namespace and manifest_ns != expected_namespace:
        raise ValueError(
            f"Manifest namespace '{manifest_ns}' does not match target namespace '{expected_namespace}'"
        )

    replicas_raw = manifest.get("spec", {}).get("replicas", 1)
    try:
        replicas = int(replicas_raw)
    except (TypeError, ValueError):
        raise ValueError(f"spec.replicas must be an integer, got: {replicas_raw!r}")

    if replicas < 0:
        raise ValueError(f"spec.replicas cannot be negative, got: {replicas}")

    pod_spec = manifest.get("spec", {}).get("template", {}).get("spec", {})
    containers = pod_spec.get("containers", [])
    if not containers:
        raise ValueError("Deployment manifest has no containers in spec.template.spec.containers")

    total_cpu = 0.0
    total_mem = 0.0
    container_summaries = []

    for c in containers:
        name = c.get("name", "unnamed")
        res = c.get("resources", {})
        requests = res.get("requests", {})
        limits   = res.get("limits", {})

        # Kubernetes behavior: if request is absent but limit is present, request defaults to limit
        cpu_val = requests.get("cpu") or limits.get("cpu")
        mem_val = requests.get("memory") or limits.get("memory")

        if cpu_val is None:
            raise ValueError(
                f"Container '{name}' has no CPU request or limit defined. "
                "Explicit resource requests are required for cost governance."
            )
        if mem_val is None:
            raise ValueError(
                f"Container '{name}' has no memory request or limit defined. "
                "Explicit resource requests are required for cost governance."
            )

        cpu_cores = parse_cpu(cpu_val)
        mem_gib   = parse_memory_gib(mem_val)

        total_cpu += cpu_cores
        total_mem += mem_gib
        container_summaries.append({
            "name": name,
            "cpu_cores": cpu_cores,
            "memory_gib": mem_gib,
        })

    return {
        "replicas":    replicas,
        "cpu_cores":   round(total_cpu, 4),
        "memory_gib":  round(total_mem, 4),
        "containers":  container_summaries,
    }


# ── Cost Calculations ─────────────────────────────────────────────────────────

def calculate_workload_monthly_cost(resources: dict, pricing: dict) -> float:
    """Compute the 730-hour full monthly cost for a workload specification."""
    h   = pricing["hours_per_month"]
    r   = resources["replicas"]
    cpu = r * resources["cpu_cores"] * pricing["cpu_core_hour_usd"] * h
    mem = r * resources["memory_gib"] * pricing["memory_gb_hour_usd"] * h
    return round(cpu + mem, 4)


def compute_forecasts(
    current_spend_mtd: float,
    before_cost: float,
    after_cost: float,
    days_in_month: int,
    days_elapsed: int,
    days_remaining: int,
) -> dict:
    """Calculate both End-of-Month (EOM) Forecast and Steady-State Monthly Run-Rate.
    
    EOM Forecast:
      Prorates the delta for the remaining days in the calendar month and adds
      it to the extrapolated month-end baseline spend.
      
    Steady-State Monthly Run-Rate:
      Represents the full 30-day ongoing monthly spend once deployed.
    """
    delta_monthly = round(after_cost - before_cost, 4)

    # Prorated delta for remaining days of the current calendar month
    proration_factor = (days_remaining / days_in_month) if days_in_month > 0 else 1.0
    delta_eom = round(delta_monthly * proration_factor, 4)

    # Extrapolated baseline without the change
    if days_elapsed > 0:
        daily_rate = current_spend_mtd / days_elapsed
        extrapolated_baseline = round(daily_rate * days_in_month, 4)
    else:
        extrapolated_baseline = before_cost

    projected_eom_spend = round(current_spend_mtd + (daily_rate * days_remaining if days_elapsed > 0 else before_cost) + delta_eom, 4)
    projected_monthly_run_rate = round(extrapolated_baseline + delta_monthly, 4)

    return {
        "delta_monthly_usd":            delta_monthly,
        "delta_eom_usd":                delta_eom,
        "projected_eom_spend_usd":       projected_eom_spend,
        "projected_monthly_run_rate_usd": projected_monthly_run_rate,
        "extrapolated_baseline_usd":     extrapolated_baseline,
        "proration_factor":              round(proration_factor, 4),
    }


# ── Staleness & Data Availability ─────────────────────────────────────────────

def verify_cost_data(
    costs_path: Path,
    namespace: str,
    max_age_hours: float = 48.0,
    as_of_override: Optional[datetime.datetime] = None,
    environment: str = "production",
) -> Tuple[bool, bool, float, str, float, dict]:
    """Verify costs.json contains the namespace/environment pair and is not stale.
    Returns: (data_available, cost_data_stale, data_age_hours, data_as_of, current_spend, raw_meta)
    """
    if not costs_path.exists():
        return False, True, 9999.0, "missing", 0.0, {}

    try:
        data = json.loads(costs_path.read_text())
    except Exception:
        return False, True, 9999.0, "corrupt", 0.0, {}

    costs_list = data.get("costs", [])
    entry = next(
        (
            c for c in costs_list
            if c.get("namespace") == namespace
            and c.get("environment") == environment
        ),
        None,
    )
    if entry is None:
        return False, True, 9999.0, "namespace_environment_not_found", 0.0, data

    try:
        current_spend = float(entry["current_spend_usd"])
    except (KeyError, TypeError, ValueError):
        return False, True, 9999.0, "invalid_current_spend", 0.0, data
    if current_spend < 0:
        return False, True, 9999.0, "negative_current_spend", 0.0, data
    data_as_of = entry.get("data_as_of") or data.get("data_as_of", "unknown")

    if data_as_of in ("unknown", "missing", "corrupt"):
        return True, True, 9999.0, data_as_of, current_spend, data

    now = as_of_override or datetime.datetime.now(datetime.timezone.utc)
    try:
        # ISO format: 2026-10-27T01:46:21Z or with offset
        ts = datetime.datetime.fromisoformat(data_as_of.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.timezone.utc)
        age_hours = round((now - ts).total_seconds() / 3600.0, 2)
    except (TypeError, ValueError):
        age_hours = 9999.0

    is_stale = age_hours > max_age_hours or age_hours < -1.0 # Future dates > 1h are also invalid
    return True, is_stale, age_hours, data_as_of, current_spend, data


# ── Output Formatter ──────────────────────────────────────────────────────────

def print_summary(report: dict) -> None:
    before = report["before"]
    after  = report["after"]
    decision = report.get("decision_preview", "UNKNOWN")

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║              COST GATE REPORT — {report['namespace']:<28} ║
╠══════════════════════════════════════════════════════════════╣
║  Environment:       {report.get('environment', 'production'):<41} ║
║  Resource Changes                                            ║
║   Replicas:         {str(before['replicas']):>6} → {str(after['replicas']):<6}                        ║
║   CPU request:      {str(before['cpu_cores']):>6} → {str(after['cpu_cores']):<6} cores                  ║
║   Memory request:   {str(before['memory_gib']):>6} → {str(after['memory_gib']):<6} GiB                  ║
╠══════════════════════════════════════════════════════════════╣
║  Workload Monthly Cost                                       ║
║   Base workload:             ${before['monthly_cost_usd']:<8.2f}/mo                  ║
║   Proposed workload:         ${after['monthly_cost_usd']:<8.2f}/mo                  ║
║   Monthly Delta:             ${report['delta_usd']:<+8.2f}/mo                  ║
╠══════════════════════════════════════════════════════════════╣
║  FinOps Projections ({report['days_elapsed']} elapsed, {report['days_remaining']} remaining in month)      ║
║   MTD Spend to date:         ${report['current_spend_usd']:<8.2f}                         ║
║   Prorated Delta this month: ${report['delta_eom_usd']:<+8.2f}                         ║
║   Projected Month-End (EOM): ${report['projected_eom_spend_usd']:<8.2f}                         ║
║   Steady-State Run-Rate:     ${report['projected_monthly_run_rate_usd']:<8.2f}/mo                  ║
║   Evaluated Spend:           ${report['projected_spend_usd']:<8.2f}/mo                  ║
║   Budget:                    ${report['budget_usd']:<8.2f}/mo                  ║
║   Utilization:               {report['budget_utilization_pct']:<6.1f}%                        ║
║   Variance:                  ${report['budget_variance_usd']:<+8.2f}                         ║
╠══════════════════════════════════════════════════════════════╣
║  Data Health                                                 ║
║   Cost data status:  {'AVAILABLE ✅' if report['data_available'] else 'MISSING ❌':<40} ║
║   Data freshness:    {f"{report['data_age_hours']:.1f}h (max {report['max_data_age_hours']}h)":<40} ║
║   Stale data flag:   {'YES (FAIL CLOSED) ❌' if report['cost_data_stale'] else 'NO (FRESH) ✅':<40} ║
║   Data timestamp:    {report['data_as_of']:<40} ║
╠══════════════════════════════════════════════════════════════╣
║  Preview Decision:   {decision:<40} ║
╚══════════════════════════════════════════════════════════════╝
""")


# ── Main ──────────────────────────────────────────────────────────────────────

def generate_report(
    base_file: str,
    proposed_file: str,
    namespace: str,
    environment: Optional[str] = None,
    override_approved: bool = False,
    override_by: str = "",
    override_reason: str = "",
    override_pr: str = "",
    reference_date: Optional[datetime.date] = None,
) -> dict:
    pricing_data = yaml.safe_load(PRICING_FILE.read_text())["pricing"]
    budgets_raw  = yaml.safe_load(BUDGETS_FILE.read_text()) or {}

    max_age_hours = float(budgets_raw.get("max_cost_data_age_hours", 48.0))
    allowed_approvers = budgets_raw.get("allowed_override_approvers", [])
    default_env = budgets_raw.get("default_environment", "production")
    target_env = environment or default_env
    if not isinstance(allowed_approvers, list):
        raise ValueError("allowed_override_approvers must be a non-empty list")
    if override_approved and not allowed_approvers:
        raise ValueError("Cannot create an override without an authorized approver allowlist")

    ns_budgets = {b["name"]: b for b in budgets_raw.get("namespaces", [])}
    if namespace not in ns_budgets:
        raise ValueError(f"Namespace '{namespace}' not found in {BUDGETS_FILE}")

    ns_config = ns_budgets[namespace]
    configured_env = ns_config.get("environment", target_env)
    if configured_env != target_env:
        raise ValueError(
            f"Namespace '{namespace}' is configured for environment '{configured_env}', "
            f"not '{target_env}'"
        )
    budget_usd = float(ns_config["monthly_budget_usd"])
    alert_threshold_pct = float(ns_config.get("alert_threshold_pct", 80))

    # Calendar dates
    today = reference_date or datetime.datetime.now(datetime.timezone.utc).date()
    _, days_in_month = calendar.monthrange(today.year, today.month)
    days_elapsed = min(today.day, days_in_month)
    days_remaining = max(0, days_in_month - days_elapsed)

    # Load and verify cost data
    data_avail, is_stale, age_hours, data_as_of, current_spend, ce_meta = verify_cost_data(
        COSTS_FILE,
        namespace,
        max_age_hours=max_age_hours,
        environment=target_env,
    )

    # If CE metadata has calendar info, prioritize it
    if ce_meta:
        days_in_month = ce_meta.get("days_in_month", days_in_month)
        days_elapsed  = ce_meta.get("days_elapsed", days_elapsed)
        days_remaining = ce_meta.get("days_remaining", days_remaining)

    # Load manifests
    base_path = Path(base_file)
    proposed_path = Path(proposed_file)

    base_manifest = yaml.safe_load(base_path.read_text()) if base_path.exists() else None
    proposed_manifest = yaml.safe_load(proposed_path.read_text())

    before = extract_resources(base_manifest, expected_namespace=namespace)
    after  = extract_resources(proposed_manifest, expected_namespace=namespace)

    before_cost = calculate_workload_monthly_cost(before, pricing_data)
    after_cost  = calculate_workload_monthly_cost(after, pricing_data)

    forecasts = compute_forecasts(
        current_spend_mtd=current_spend,
        before_cost=before_cost,
        after_cost=after_cost,
        days_in_month=days_in_month,
        days_elapsed=days_elapsed,
        days_remaining=days_remaining,
    )

    # FinOps Gate evaluation uses the steady-state monthly run-rate OR EOM forecast (whichever is higher)
    evaluated_spend = max(forecasts["projected_eom_spend_usd"], forecasts["projected_monthly_run_rate_usd"])
    utilization = round(evaluated_spend / budget_usd * 100, 1) if budget_usd > 0 else 999.0
    variance    = round(budget_usd - evaluated_spend, 4)

    # Determine preview decision
    if not data_avail or is_stale:
        decision = "❌ BLOCKED (DATA UNAVAILABLE OR STALE)"
    elif evaluated_spend > budget_usd:
        if override_approved and override_by in allowed_approvers and override_reason:
            decision = "⚠️  PASS WITH OVERRIDE"
        else:
            decision = "❌ BLOCKED (EXCEEDS BUDGET)"
    else:
        decision = "✅ PASS"

    report = {
        "namespace":                  namespace,
        "environment":                target_env,
        "period":                     ce_meta.get("period", today.strftime("%Y-%m")),
        "before":                     {**before, "monthly_cost_usd": before_cost},
        "after":                      {**after,  "monthly_cost_usd": after_cost},
        "delta_usd":                  forecasts["delta_monthly_usd"],
        "delta_eom_usd":              forecasts["delta_eom_usd"],
        "current_spend_usd":          round(current_spend, 4),
        "projected_eom_spend_usd":    forecasts["projected_eom_spend_usd"],
        "projected_monthly_run_rate_usd": forecasts["projected_monthly_run_rate_usd"],
        "projected_spend_usd":        round(evaluated_spend, 4),
        "budget_usd":                 budget_usd,
        "alert_threshold_pct":        alert_threshold_pct,
        "budget_utilization_pct":     utilization,
        "budget_variance_usd":        variance,
        "days_in_month":              days_in_month,
        "days_elapsed":               days_elapsed,
        "days_remaining":             days_remaining,
        "data_available":             data_avail,
        "cost_data_stale":            is_stale,
        "data_age_hours":             age_hours,
        "max_data_age_hours":         max_age_hours,
        "data_as_of":                 data_as_of,
        "pricing_version":            pricing_data.get("version", "unknown"),
        "allowed_override_approvers": allowed_approvers,
        "decision_preview":           decision,
        "note": (
            "Approximation only. Shared node, control-plane, autoscaling, "
            "storage, and network costs are not included."
        ),
    }

    if override_approved:
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        report["override"] = {
            "approved":     True,
            "approved_by":  override_by,
            "reason":       override_reason,
            "pull_request": override_pr,
            "timestamp":    now_str,
        }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(report, indent=2))
    print_summary(report)
    print(f"✅ Cost report written to {REPORT_FILE}")
    return report


def main():
    parser = argparse.ArgumentParser(description="Estimate Kubernetes deployment cost delta with FinOps forecasting")
    parser.add_argument("--base",              required=True, help="Path to base manifest")
    parser.add_argument("--proposed",          required=True, help="Path to proposed manifest")
    parser.add_argument("--namespace",         required=True, help="Kubernetes namespace")
    parser.add_argument("--environment",       default=None,  help="Environment name")
    parser.add_argument("--override-approved",  action="store_true", default=False)
    parser.add_argument("--override-by",        default="")
    parser.add_argument("--override-reason",    default="")
    parser.add_argument("--override-pr",        default="")
    args = parser.parse_args()

    generate_report(
        base_file=args.base,
        proposed_file=args.proposed,
        namespace=args.namespace,
        environment=args.environment,
        override_approved=args.override_approved,
        override_by=args.override_by,
        override_reason=args.override_reason,
        override_pr=args.override_pr,
    )


if __name__ == "__main__":
    main()
