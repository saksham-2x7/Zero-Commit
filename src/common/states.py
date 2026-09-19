# DynamoDB literal ConditionExpressions
T0_CHG_COND = "attribute_not_exists(pk)"
T0_SGLOCK_COND = "attribute_not_exists(pk) OR lock_until < :now"

T1_COND = "#st = :P AND apply_done = :false AND attribute_not_exists(apply_lease_until)"
T1B_COND = "#st = :P"

T2_COND = "#st = :P AND apply_done = :true AND expires_at > :now"

T3_COND = "#st = :P AND (apply_done = :true OR attribute_not_exists(apply_lease_until) OR apply_lease_until < :now)"

T4_COND = "#st = :R AND revert_lease_until < :now"

# T5, T6, T7 all use the same condition
T5_COND = "#st = :R AND revert_owner = :me"

# T8
T8_COND = "#st = :P AND apply_done = :false"

# Valid status values
STATUS_PENDING = "PENDING"
STATUS_CONFIRMED = "CONFIRMED"
STATUS_REVERTING = "REVERTING"
STATUS_REVERTED = "REVERTED"
STATUS_PARTIAL_REVERT = "PARTIAL_REVERT"
STATUS_FAILED = "FAILED"
