import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Literal

from linersync.domain.tracks import ServiceId


class FieldKind(StrEnum):
    TEXT = "text"
    SECRET = "secret"
    MULTILINE_SECRET = "multiline_secret"


class StepKind(StrEnum):
    INFO = "info"
    FORM = "form"
    OAUTH = "oauth"
    BROWSER_SIGN_IN = "browser_sign_in"
    DEVICE_CODE = "device_code"
    TEST = "test"


@dataclass(frozen=True, slots=True)
class SetupField:
    key: str
    label: str
    kind: FieldKind = FieldKind.TEXT
    help: str = ""
    pattern: str | None = None
    pattern_hint: str = ""
    placeholder: str = ""
    required: bool = True


@dataclass(frozen=True, slots=True)
class Link:
    label: str
    url: str


@dataclass(frozen=True, slots=True)
class Annotation:
    """A highlighted region, as fractions (0..1) of the image."""

    x: float
    y: float
    width: float
    height: float
    label: str


@dataclass(frozen=True, slots=True)
class Screenshot:
    resource: str  # package-relative path
    caption: str
    annotations: tuple[Annotation, ...] = ()


@dataclass(frozen=True, slots=True)
class CopyValue:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class SetupStep:
    step_id: str
    title: str
    kind: StepKind
    body: str = ""
    fields: tuple[SetupField, ...] = ()
    links: tuple[Link, ...] = ()
    screenshots: tuple[Screenshot, ...] = ()
    copy_values: tuple[CopyValue, ...] = ()


@dataclass(frozen=True, slots=True)
class SetupGuide:
    service: ServiceId
    display_name: str
    category: Literal["streaming", "self_hosted"]
    prerequisites: tuple[str, ...]
    steps: tuple[SetupStep, ...]

    def fields(self) -> tuple[SetupField, ...]:
        return tuple(
            f for step in self.steps if step.kind is StepKind.FORM for f in step.fields
        )


def validate_field(field: SetupField, value: str) -> str | None:
    value = value.strip()
    if not value:
        return "Required" if field.required else None
    if field.pattern is not None and re.fullmatch(field.pattern, value) is None:
        return field.pattern_hint or "This doesn't look right"
    return None


def validate_values(guide: SetupGuide, values: Mapping[str, str]) -> dict[str, str]:
    problems: dict[str, str] = {}
    for field in guide.fields():
        message = validate_field(field, values.get(field.key, ""))
        if message is not None:
            problems[field.key] = message
    return problems


class _Context(dict[str, str]):
    def __missing__(self, key: str) -> str:
        raise ValueError(f"Unknown placeholder {{{key}}}")


def render_guide(guide: SetupGuide, context: Mapping[str, str]) -> SetupGuide:
    ctx = _Context(context)

    def fill(text: str) -> str:
        return text.format_map(ctx)

    steps = tuple(
        replace(
            step,
            body=fill(step.body),
            links=tuple(replace(link, url=fill(link.url)) for link in step.links),
            copy_values=tuple(
                replace(cv, value=fill(cv.value)) for cv in step.copy_values
            ),
        )
        for step in guide.steps
    )
    return replace(guide, steps=steps)


_SERVICE_ID = re.compile(r"[a-z][a-z0-9_]*")


def check_guide(guide: SetupGuide) -> list[str]:
    problems: list[str] = []
    if _SERVICE_ID.fullmatch(guide.service) is None:
        problems.append(f"invalid service id {guide.service!r}")
    seen_steps: set[str] = set()
    seen_keys: set[str] = set()
    for step in guide.steps:
        if step.step_id in seen_steps:
            problems.append(f"duplicate step_id {step.step_id!r}")
        seen_steps.add(step.step_id)
        for field in step.fields:
            if field.key in seen_keys:
                problems.append(f"duplicate field key {field.key!r}")
            seen_keys.add(field.key)
            if field.pattern is not None:
                try:
                    re.compile(field.pattern)
                except re.error as exc:
                    problems.append(
                        f"field {field.key!r} has an invalid pattern: {exc}"
                    )
        for shot in step.screenshots:
            for a in shot.annotations:
                if not _in_unit(a):
                    problems.append(
                        f"annotation {a.label!r} in {step.step_id!r} is outside 0..1"
                    )
    if not guide.steps or guide.steps[-1].kind is not StepKind.TEST:
        problems.append("the last step must be a TEST step")
    return problems


def _in_unit(a: Annotation) -> bool:
    return (
        0 <= a.x <= 1
        and 0 <= a.y <= 1
        and 0 <= a.width <= 1
        and 0 <= a.height <= 1
        and a.x + a.width <= 1
        and a.y + a.height <= 1
    )
