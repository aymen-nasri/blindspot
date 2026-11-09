"""Fault files: one YAML file per fault, validated strictly, so a typo fails loudly instead of quietly."""

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

STEP_TYPES = (
    "deployment-patch",
    "corefile-block",
    "pod-netem",
    "route-mtu",
    "apply",
    "scale-cr",
    "noop",
    "node-fill-disk",
    "node-fill-inodes",
    "node-stress-memory",
    "node-stop-service",
    "node-clock-skew",
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Clause(Strict):
    """An alert is relevant if its name matches `alertname` and every listed label matches its pattern."""

    alertname: str = ".*"
    labels: dict[str, str] = {}

    @field_validator("alertname")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as error:
            raise ValueError(f"not a valid pattern: {error}") from error
        return value

    @field_validator("labels")
    @classmethod
    def _label_patterns_compile(cls, value: dict[str, str]) -> dict[str, str]:
        for pattern in value.values():
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValueError(f"not a valid pattern: {error}") from error
        return value


class Harm(Strict):
    probe: Literal["web", "big", "api"] | None = None
    p95_ms: int | None = Field(default=None, gt=0)
    gone: Literal["alertmanager", "prometheus"] | None = None

    @model_validator(mode="after")
    def _says_something(self) -> "Harm":
        if self.probe is None and self.gone is None:
            raise ValueError("harm needs a probe, a component that must be gone, or both")
        if self.p95_ms is not None and self.probe is None:
            raise ValueError("p95_ms needs a probe")
        return self


class Step(Strict):
    type: Literal[STEP_TYPES]  # type: ignore[valid-type]
    params: dict[str, Any] = {}


class Fault(Strict):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    number: int = Field(ge=0)
    name: str
    fault_class: Literal["harm", "early-warning", "monitoring", "control"] = Field(alias="class")
    runs_on: list[Literal["kind", "vm"]]
    window_minutes: int = Field(default=30, ge=5)
    delivery: Literal["on", "off"] = "on"
    inject: list[Step]
    harm: Harm | None
    relevant: list[Clause]
    expect: Literal["caught", "not caught", "invalid: no harm", "invalid: receiver"] | None = None
    why: str

    @model_validator(mode="after")
    def _consistent(self) -> "Fault":
        if self.fault_class != "control":
            if self.expect is not None:
                raise ValueError("only controls declare an expected result")
            if self.harm is None:
                raise ValueError("a real fault needs a harm rule")
            if not self.inject:
                raise ValueError("a real fault needs at least one step")
            if not self.relevant:
                raise ValueError("a real fault needs a list of relevant alerts")
        elif self.expect is None:
            raise ValueError("a control must declare what it expects")
        return self

    def is_relevant(self, labels: dict[str, str]) -> bool:
        name = labels.get("alertname", "")
        if name in NEVER_RELEVANT:
            return False
        return any(
            re.fullmatch(c.alertname, name) and all(re.fullmatch(v, labels.get(k, "")) for k, v in c.labels.items())
            for c in self.relevant
        )


# Never evidence of anything on their own.
NEVER_RELEVANT = frozenset({"Watchdog", "InfoInhibitor"})


def load(directory: Path) -> dict[str, Fault]:
    faults: dict[str, Fault] = {}
    for path in sorted(directory.rglob("*.yaml")):
        fault = Fault.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if fault.id != path.stem:
            raise ValueError(f"{path}: id {fault.id!r} must match the file name")
        if fault.id in faults:
            raise ValueError(f"{path}: duplicate id {fault.id!r}")
        faults[fault.id] = fault
    return faults
