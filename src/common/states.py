"""
Deadman state machine definitions and transition condition expressions.
Spec §4 State Machine.
"""
from typing import Dict, Any

# States
STATUS_PENDING = "PENDING"
STATUS_CONFIRMED = "CONFIRMED"
STATUS_REVERTING = "REVERTING"
STATUS_REVERTED = "REVERTED"
STATUS_PARTIAL_REVERT = "PARTIAL_REVERT"
STATUS_FAILED = "FAILED"

ALL_STATUSES = {
    STATUS_PENDING,
    STATUS_CONFIRMED,
    STATUS_REVERTING,
    STATUS_REVERTED,
    STATUS_PARTIAL_REVERT,
    STATUS_FAILED,
}

TERMINAL_STATUSES = {
    STATUS_CONFIRMED,
    STATUS_REVERTED,
    STATUS_PARTIAL_REVERT,
    STATUS_FAILED,
}

# Revert Triggers
TRIGGER_SCHEDULE = "SCHEDULE"
TRIGGER_MANUAL = "MANUAL"
TRIGGER_APPLY_FAILURE = "APPLY_FAILURE"

ALL_TRIGGERS = {
    TRIGGER_SCHEDULE,
    TRIGGER_MANUAL,
    TRIGGER_APPLY_FAILURE,
}

# Revert Report Results
RESULT_REVERTED = "REVERTED"
RESULT_SKIPPED = "SKIPPED_ALREADY_SATISFIED"
RESULT_ERROR = "ERROR"

# Literal ConditionExpressions from Spec §4
# Note: 'status' is a DynamoDB reserved keyword, so '#status' is used in expressions.
COND_T0_CHG = "attribute_not_exists(pk)"
COND_T0_SGLOCK = "attribute_not_exists(pk) OR lock_until < :now"

COND_T1_FENCE = "#status = :P AND apply_done = :false AND attribute_not_exists(apply_lease_until)"
COND_T1B_CUT_DONE = "#status = :P"

COND_T2_CONFIRM = "#status = :P AND apply_done = :true AND expires_at > :now"

COND_T3_REVERT = "#status = :P AND (apply_done = :true OR attribute_not_exists(apply_lease_until) OR apply_lease_until < :now)"

COND_T4_TAKEOVER = "#status = :R AND revert_lease_until < :now"

COND_T5_REVERTED = "#status = :R AND revert_owner = :me"
COND_T6_PARTIAL_REVERT = "#status = :R AND revert_owner = :me"
COND_T7_FAILED = "#status = :R AND revert_owner = :me"

COND_T8_SCHEDULE_FAILED = "#status = :P AND apply_done = :false"

# Transition metadata table
TRANSITIONS: Dict[str, Dict[str, Any]] = {
    "T0": {
        "from": None,
        "to": STATUS_PENDING,
        "trigger": "apply",
        "condition_chg": COND_T0_CHG,
        "condition_sglock": COND_T0_SGLOCK,
    },
    "T1": {
        "from": STATUS_PENDING,
        "to": STATUS_PENDING,
        "trigger": "apply (fence)",
        "condition": COND_T1_FENCE,
    },
    "T1b": {
        "from": STATUS_PENDING,
        "to": STATUS_PENDING,
        "trigger": "apply (cut done)",
        "condition": COND_T1B_CUT_DONE,
    },
    "T2": {
        "from": STATUS_PENDING,
        "to": STATUS_CONFIRMED,
        "trigger": "confirm",
        "condition": COND_T2_CONFIRM,
    },
    "T3": {
        "from": STATUS_PENDING,
        "to": STATUS_REVERTING,
        "trigger": "revert (SCHEDULE | MANUAL | APPLY_FAILURE)",
        "condition": COND_T3_REVERT,
    },
    "T4": {
        "from": STATUS_REVERTING,
        "to": STATUS_REVERTING,
        "trigger": "takeover",
        "condition": COND_T4_TAKEOVER,
    },
    "T5": {
        "from": STATUS_REVERTING,
        "to": STATUS_REVERTED,
        "trigger": "revert all done",
        "condition": COND_T5_REVERTED,
    },
    "T6": {
        "from": STATUS_REVERTING,
        "to": STATUS_PARTIAL_REVERT,
        "trigger": "revert partial error",
        "condition": COND_T6_PARTIAL_REVERT,
    },
    "T7": {
        "from": STATUS_REVERTING,
        "to": STATUS_FAILED,
        "trigger": "revert precondition error",
        "condition": COND_T7_FAILED,
    },
    "T8": {
        "from": STATUS_PENDING,
        "to": STATUS_FAILED,
        "trigger": "CreateSchedule failed",
        "condition": COND_T8_SCHEDULE_FAILED,
    },
}
