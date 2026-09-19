# src/common/states.py

class State:
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REVERTING = "REVERTING"
    REVERTED = "REVERTED"
    PARTIAL_REVERT = "PARTIAL_REVERT"
    FAILED = "FAILED"

# Condition Expressions for DynamoDB state transitions
# T0: CREATE -> PENDING
T0_COND = "attribute_not_exists(pk)"

# T1: PENDING -> REVERTING
T1_COND = "#st = :expected_state"
T1_VALS = {":expected_state": State.PENDING}

# T2: PENDING -> CONFIRMED
T2_COND = "#st = :expected_state"
T2_VALS = {":expected_state": State.PENDING}

# T3: REVERTING -> REVERTED
T3_COND = "#st = :expected_state"
T3_VALS = {":expected_state": State.REVERTING}

# T4: REVERTING -> PARTIAL_REVERT
T4_COND = "#st = :expected_state"
T4_VALS = {":expected_state": State.REVERTING}

# T5: REVERTING -> FAILED
T5_COND = "#st = :expected_state"
T5_VALS = {":expected_state": State.REVERTING}

# T6: PENDING -> FAILED
T6_COND = "#st = :expected_state"
T6_VALS = {":expected_state": State.PENDING}

# T7: CONFIRMED -> FAILED
T7_COND = "#st = :expected_state"
T7_VALS = {":expected_state": State.CONFIRMED}

# T8: CREATE -> FAILED (Nothing applied)
# This usually happens right after T0 if CreateSchedule fails
T8_COND = "#st = :expected_state"
T8_VALS = {":expected_state": State.PENDING}
