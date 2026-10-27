package cost_gate_test

import data.cost_gate
import future.keywords.if

# 1. Test Pass: Valid under-budget deployment
test_pass_under_budget if {
    input := {
        "namespace": "team-checkout",
        "budget_usd": 30.0,
        "current_spend_usd": 10.0,
        "projected_spend_usd": 20.0,
        "budget_utilization_pct": 66.7,
        "delta_usd": 5.0,
        "data_available": true,
        "cost_data_stale": false,
        "data_age_hours": 1.0,
        "max_data_age_hours": 48.0,
        "data_as_of": "2026-10-27T00:00:00Z",
        "alert_threshold_pct": 80.0,
        "after": {
            "replicas": 2,
            "cpu_cores": 0.5,
            "memory_gib": 0.5
        }
    }
    count(cost_gate.deny) == 0 with input as input
}

# 2. Test Block: Over-budget deployment with no override
test_block_over_budget if {
    input := {
        "namespace": "team-checkout",
        "budget_usd": 30.0,
        "current_spend_usd": 15.0,
        "projected_spend_usd": 45.0,
        "budget_utilization_pct": 150.0,
        "delta_usd": 15.0,
        "data_available": true,
        "cost_data_stale": false,
        "data_age_hours": 1.0,
        "max_data_age_hours": 48.0,
        "data_as_of": "2026-10-27T00:00:00Z",
        "alert_threshold_pct": 80.0,
        "after": {
            "replicas": 4,
            "cpu_cores": 1.0,
            "memory_gib": 1.0
        }
    }
    count(cost_gate.deny) > 0 with input as input
}

# 3. Test Pass with valid authorized override
test_pass_with_authorized_override if {
    input := {
        "namespace": "team-checkout",
        "budget_usd": 30.0,
        "current_spend_usd": 15.0,
        "projected_spend_usd": 45.0,
        "budget_utilization_pct": 150.0,
        "delta_usd": 15.0,
        "data_available": true,
        "cost_data_stale": false,
        "data_age_hours": 1.0,
        "max_data_age_hours": 48.0,
        "data_as_of": "2026-10-27T00:00:00Z",
        "alert_threshold_pct": 80.0,
        "allowed_override_approvers": ["finops-lead", "platform-admin"],
        "override": {
            "approved": true,
            "approved_by": "finops-lead",
            "reason": "Production load testing"
        },
        "after": {
            "replicas": 4,
            "cpu_cores": 1.0,
            "memory_gib": 1.0
        }
    }
    count(cost_gate.deny) == 0 with input as input
}

# 4. Test Block on unauthorized override approver
test_block_unauthorized_override if {
    input := {
        "namespace": "team-checkout",
        "budget_usd": 30.0,
        "current_spend_usd": 15.0,
        "projected_spend_usd": 45.0,
        "budget_utilization_pct": 150.0,
        "delta_usd": 15.0,
        "data_available": true,
        "cost_data_stale": false,
        "data_age_hours": 1.0,
        "max_data_age_hours": 48.0,
        "data_as_of": "2026-10-27T00:00:00Z",
        "alert_threshold_pct": 80.0,
        "allowed_override_approvers": ["finops-lead"],
        "override": {
            "approved": true,
            "approved_by": "unauthorized-dev",
            "reason": "I want to bypass"
        },
        "after": {
            "replicas": 4,
            "cpu_cores": 1.0,
            "memory_gib": 1.0
        }
    }
    count(cost_gate.deny) > 0 with input as input
}

# 5. Test Fail Closed on stale cost data
test_fail_closed_stale_data if {
    input := {
        "namespace": "team-checkout",
        "budget_usd": 30.0,
        "current_spend_usd": 10.0,
        "projected_spend_usd": 15.0,
        "budget_utilization_pct": 50.0,
        "delta_usd": 2.0,
        "data_available": true,
        "cost_data_stale": true,
        "data_age_hours": 72.0,
        "max_data_age_hours": 48.0,
        "data_as_of": "2026-10-24T00:00:00Z",
        "after": {
            "replicas": 2,
            "cpu_cores": 0.5,
            "memory_gib": 0.5
        }
    }
    count(cost_gate.deny) > 0 with input as input
}

# 6. Test Fail Closed on missing cost data
test_fail_closed_missing_data if {
    input := {
        "namespace": "team-checkout",
        "budget_usd": 30.0,
        "current_spend_usd": 0.0,
        "projected_spend_usd": 15.0,
        "data_available": false,
        "cost_data_stale": true,
        "data_as_of": "unknown",
        "after": {
            "replicas": 2,
            "cpu_cores": 0.5,
            "memory_gib": 0.5
        }
    }
    count(cost_gate.deny) > 0 with input as input
}

# 7. Test fail closed when override allowlist is empty
test_block_override_without_allowlist if {
    input := {
        "namespace": "team-checkout",
        "budget_usd": 30.0,
        "current_spend_usd": 15.0,
        "projected_spend_usd": 45.0,
        "budget_utilization_pct": 150.0,
        "delta_usd": 15.0,
        "data_available": true,
        "cost_data_stale": false,
        "data_age_hours": 1.0,
        "max_data_age_hours": 48.0,
        "data_as_of": "2026-10-27T00:00:00Z",
        "alert_threshold_pct": 80.0,
        "allowed_override_approvers": [],
        "override": {
            "approved": true,
            "approved_by": "any-user",
            "reason": "Untrusted bypass attempt"
        },
        "after": {
            "replicas": 2,
            "cpu_cores": 0.5,
            "memory_gib": 0.5
        }
    }
    count(cost_gate.deny) > 0 with input as input
}
