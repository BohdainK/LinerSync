import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from typing import Any

from linersync.domain.operations import MemberPlan


@dataclass(frozen=True, slots=True)
class BrakeConfig:
    max_fraction: float = 0.20
    max_count: int = 50


@dataclass(frozen=True, slots=True)
class BrakeTrip:
    member_id: str
    removals: int
    size: int
    reason: str


def check_brake(
    plan: MemberPlan, current_size: int, cfg: BrakeConfig
) -> BrakeTrip | None:
    removals = plan.removal_count
    too_many = removals > cfg.max_count
    too_large_share = current_size > 0 and removals / current_size > cfg.max_fraction
    if not (too_many or too_large_share):
        return None
    return BrakeTrip(
        member_id=plan.member_id,
        removals=removals,
        size=current_size,
        reason=f"This run would remove {removals} of {current_size} songs",
    )


def _content(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return [type(value).__name__] + [
            [f.name, _content(getattr(value, f.name))]
            for f in fields(value)
            if f.name != "op_id"
        ]
    if isinstance(value, tuple | list):
        return [_content(v) for v in value]
    return value


def fingerprint(plan: MemberPlan) -> str:
    """sha256 hex of the ops' content (type and fields, not op_id), in order."""
    payload = json.dumps(
        [_content(op) for op in plan.operations],
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()
