package cost_gate

import future.keywords.if
import future.keywords.in
import future.keywords.contains

# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMA & DATA HEALTH CHECKS — Fail closed if reports are invalid or stale
# ─────────────────────────────────────────────────────────────────────────────

# 1. Namespace validation
deny contains msg if {
    not is_string(input.namespace)
    msg := "BLOCKED: 'namespace' field is missing or not a string"
}

deny contains msg if {
    is_string(input.namespace)
    input.namespace == ""
    msg := "BLOCKED: 'namespace' cannot be empty"
}

# 2. Budget configuration check
deny contains msg if {
    not is_number(input.budget_usd)
    msg := sprintf("BLOCKED: 'budget_usd' is missing or not numeric for namespace '%v'", [object.get(input, "namespace", "?")])
}

deny contains msg if {
    is_number(input.budget_usd)
    input.budget_usd <= 0
    msg := sprintf("BLOCKED: Configured budget must be positive for namespace '%v' (got: $%.2f)", [input.namespace, input.budget_usd])
}

# 3. Spend data validation (Fail closed; 0.0 is a valid number, but null/missing is not)
deny contains msg if {
    not is_number(input.current_spend_usd)
    msg := "BLOCKED: 'current_spend_usd' is missing or invalid — cost data unavailable"
}

deny contains msg if {
    not is_number(input.projected_spend_usd)
    msg := "BLOCKED: 'projected_spend_usd' is missing or invalid"
}

deny contains msg if {
    is_number(input.current_spend_usd)
    input.current_spend_usd < 0
    msg := "BLOCKED: 'current_spend_usd' cannot be negative"
}

deny contains msg if {
    is_number(input.projected_spend_usd)
    input.projected_spend_usd < 0
    msg := "BLOCKED: 'projected_spend_usd' cannot be negative"
}

deny contains msg if {
    not is_number(input.budget_utilization_pct)
    msg := "BLOCKED: 'budget_utilization_pct' is missing or invalid"
}

deny contains msg if {
    not is_number(input.alert_threshold_pct)
    msg := "BLOCKED: 'alert_threshold_pct' is missing or invalid"
}

# 4. Data availability flag
deny contains msg if {
    not is_boolean(input.data_available)
    msg := "BLOCKED: 'data_available' must be present and boolean"
}

deny contains msg if {
    input.data_available != true
    msg := sprintf("BLOCKED: Cost data is unavailable for namespace '%v' — failing closed", [input.namespace])
}

# 5. Data staleness check (Fail closed if cost data exceeds threshold)
deny contains msg if {
    not is_boolean(input.cost_data_stale)
    msg := "BLOCKED: 'cost_data_stale' must be present and boolean"
}

deny contains msg if {
    input.cost_data_stale != false
    msg := sprintf(
        "BLOCKED: Cost data is stale (age: %.1f hours, max allowed: %.1f hours) — failing closed",
        [input.data_age_hours, input.max_data_age_hours]
    )
}

deny contains msg if {
    not is_string(input.data_as_of)
    msg := "BLOCKED: Cost data timestamp (data_as_of) is missing"
}

deny contains msg if {
    lower(input.data_as_of) in {"unknown", "missing", "corrupt", ""}
    msg := "BLOCKED: Cost data timestamp is unknown — data freshness cannot be verified"
}

deny contains msg if {
    not is_number(input.data_age_hours)
    msg := "BLOCKED: 'data_age_hours' is missing or invalid"
}

deny contains msg if {
    not is_number(input.max_data_age_hours)
    msg := "BLOCKED: 'max_data_age_hours' is missing or invalid"
}

deny contains msg if {
    is_number(input.data_age_hours)
    input.data_age_hours < 0
    msg := "BLOCKED: 'data_age_hours' cannot be negative"
}

deny contains msg if {
    is_number(input.max_data_age_hours)
    input.max_data_age_hours <= 0
    msg := "BLOCKED: 'max_data_age_hours' must be positive"
}

# 6. Manifest resource integrity
deny contains msg if {
    not is_number(input.after.replicas)
    msg := "BLOCKED: Proposed manifest has missing or non-numeric replicas"
}

deny contains msg if {
    input.after.replicas < 0
    msg := sprintf("BLOCKED: Proposed replicas cannot be negative (got: %v)", [input.after.replicas])
}

deny contains msg if {
    not is_number(input.after.cpu_cores)
    msg := "BLOCKED: Proposed manifest is missing valid CPU resource requests"
}

deny contains msg if {
    not is_number(input.after.memory_gib)
    msg := "BLOCKED: Proposed manifest is missing valid memory resource requests"
}

deny contains msg if {
    is_number(input.after.replicas)
    input.after.replicas == 0
    msg := "BLOCKED: Proposed replicas cannot be zero for this deployment"
}

deny contains msg if {
    is_number(input.after.cpu_cores)
    input.after.cpu_cores <= 0
    msg := "BLOCKED: Proposed CPU requests must be positive"
}

deny contains msg if {
    is_number(input.after.memory_gib)
    input.after.memory_gib <= 0
    msg := "BLOCKED: Proposed memory requests must be positive"
}

deny contains msg if {
    not is_number(input.delta_usd)
    msg := "BLOCKED: 'delta_usd' is missing or invalid"
}


# ─────────────────────────────────────────────────────────────────────────────
#  BUDGET ENFORCEMENT & OVERRIDE GOVERNANCE
# ─────────────────────────────────────────────────────────────────────────────

# 7. Budget exceeded without valid override
deny contains msg if {
    input.projected_spend_usd > input.budget_usd
    not has_valid_override
    msg := sprintf(
        "BLOCKED: Projected spend $%.2f exceeds budget $%.2f for namespace '%s' (excess: $%.2f, utilization: %.1f%%)",
        [
            input.projected_spend_usd,
            input.budget_usd,
            input.namespace,
            input.projected_spend_usd - input.budget_usd,
            input.budget_utilization_pct,
        ]
    )
}

# 8. Override security: Missing required metadata
deny contains msg if {
    input.override.approved == true
    not is_string(input.override.approved_by)
    msg := "BLOCKED: Override flag is set but 'approved_by' is missing or invalid"
}

deny contains msg if {
    input.override.approved == true
    input.override.approved_by == ""
    msg := "BLOCKED: Override flag is set but 'approved_by' is empty"
}

deny contains msg if {
    input.override.approved == true
    not is_string(input.override.reason)
    msg := "BLOCKED: Override flag is set but 'reason' is missing"
}

deny contains msg if {
    input.override.approved == true
    input.override.reason == ""
    msg := "BLOCKED: Override flag is set but 'reason' is empty"
}

# 9. Override security: Unauthorized approver check
deny contains msg if {
    input.override.approved == true
    is_array(input.allowed_override_approvers)
    count(input.allowed_override_approvers) > 0
    not input.override.approved_by in input.allowed_override_approvers
    msg := sprintf(
        "BLOCKED: Override approved by unauthorized user '%s'. Must be approved by one of: %v",
        [input.override.approved_by, input.allowed_override_approvers]
    )
}

deny contains msg if {
    input.override.approved == true
    not is_array(input.allowed_override_approvers)
    msg := "BLOCKED: Override approver allowlist is missing or invalid"
}

deny contains msg if {
    input.override.approved == true
    is_array(input.allowed_override_approvers)
    count(input.allowed_override_approvers) == 0
    msg := "BLOCKED: Override approver allowlist is empty"
}


# ─────────────────────────────────────────────────────────────────────────────
#  WARN RULES — Advisory alerts using configured threshold
# ─────────────────────────────────────────────────────────────────────────────

warn contains msg if {
    threshold := input.alert_threshold_pct
    input.budget_utilization_pct >= threshold
    input.budget_utilization_pct <= 100
    msg := sprintf(
        "WARNING: Budget utilization %.1f%% for namespace '%s' is at or above the configured %v%% alert threshold",
        [input.budget_utilization_pct, input.namespace, threshold]
    )
}

warn contains msg if {
    input.delta_usd > 0
    msg := sprintf(
        "INFO: This change increases estimated monthly workload cost by $%.2f",
        [input.delta_usd]
    )
}


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

has_valid_override if {
    input.override.approved == true
    is_string(input.override.approved_by)
    input.override.approved_by != ""
    is_string(input.override.reason)
    input.override.reason != ""
    approver_authorized
}

approver_authorized if {
    is_array(input.allowed_override_approvers)
    count(input.allowed_override_approvers) > 0
    input.override.approved_by in input.allowed_override_approvers
}
