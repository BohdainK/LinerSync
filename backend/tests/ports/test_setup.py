from dataclasses import replace

import pytest

from linersync.domain.tracks import ServiceId
from linersync.ports.setup import (
    Annotation,
    CopyValue,
    FieldKind,
    Link,
    Screenshot,
    SetupField,
    SetupGuide,
    SetupStep,
    StepKind,
    check_guide,
    render_guide,
    validate_field,
    validate_values,
)


def _guide(
    steps: tuple[SetupStep, ...] | None = None, service: str = "spotify"
) -> SetupGuide:
    if steps is None:
        steps = (
            SetupStep(
                "form",
                "Credentials",
                StepKind.FORM,
                fields=(
                    SetupField(
                        "client_id",
                        "Client ID",
                        pattern=r"[a-f0-9]{4}",
                        pattern_hint="4 hex",
                    ),
                    SetupField(
                        "secret", "Secret", kind=FieldKind.SECRET, required=False
                    ),
                ),
            ),
            SetupStep("test", "Test", StepKind.TEST),
        )
    return SetupGuide(ServiceId(service), "Svc", "streaming", (), steps)


def test_validate_field_required_pattern_and_ok() -> None:
    f = SetupField("k", "K", pattern=r"[a-f0-9]{4}", pattern_hint="4 hex")
    assert validate_field(f, "   ") == "Required"
    assert validate_field(f, "zzzz") == "4 hex"
    assert validate_field(f, " ab12 ") is None
    assert (
        validate_field(SetupField("k", "K", pattern="a"), "b")
        == "This doesn't look right"
    )
    assert validate_field(SetupField("k", "K", required=False), "") is None


def test_validate_values_only_reports_failures() -> None:
    g = _guide()
    assert validate_values(g, {"client_id": "ab12"}) == {}
    assert validate_values(g, {"client_id": "nope", "secret": ""}) == {
        "client_id": "4 hex"
    }
    assert validate_values(g, {}) == {"client_id": "Required"}


def test_render_guide_substitutes_and_keeps_literal_braces() -> None:
    step = SetupStep(
        "info",
        "T",
        StepKind.INFO,
        body="Open {host} and {{keep}}",
        links=(Link("l", "{host}/x"),),
        copy_values=(CopyValue("c", "{host}/cb"),),
    )
    out = render_guide(
        _guide((step, SetupStep("test", "t", StepKind.TEST))), {"host": "h"}
    )
    s = out.steps[0]
    assert s.body == "Open h and {keep}"
    assert s.links[0].url == "h/x"
    assert s.copy_values[0].value == "h/cb"
    assert s.title == "T"


def test_render_guide_unknown_placeholder_raises() -> None:
    step = SetupStep("info", "T", StepKind.INFO, body="{nope}")
    with pytest.raises(ValueError, match="nope"):
        render_guide(_guide((step,)), {})


def test_guide_fields_in_step_order() -> None:
    assert [f.key for f in _guide().fields()] == ["client_id", "secret"]


def test_check_guide_reports_each_problem() -> None:
    assert check_guide(_guide()) == []
    test = SetupStep("test", "t", StepKind.TEST)
    info = SetupStep("a", "t", StepKind.INFO)

    def form(*fields: SetupField) -> SetupStep:
        return SetupStep("f", "t", StepKind.FORM, fields=fields)

    bad_annotation = SetupStep(
        "s",
        "t",
        StepKind.INFO,
        screenshots=(Screenshot("a.png", "c", (Annotation(0.5, 0.5, 0.6, 1.2, "x"),)),),
    )
    cases = {
        "duplicate step": _guide((info, info, test)),
        "duplicate field": _guide(
            (form(SetupField("k", "K"), SetupField("k", "K2")), test)
        ),
        "pattern": _guide((form(SetupField("k", "K", pattern="(")), test)),
        "annotation": _guide((bad_annotation, test)),
        "last step": _guide((test, info)),
        "service": _guide(service="Bad-Id"),
    }
    for name, guide in cases.items():
        problems = check_guide(guide)
        assert problems, name
        assert all(isinstance(p, str) for p in problems)
    assert any("a" in p for p in check_guide(cases["duplicate step"]))
    assert check_guide(replace(_guide(), steps=())) != []
