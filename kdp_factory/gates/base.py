"""Gates: checks that can fail the build and stop it.

Two rules hold for everything in this package.

1. **A gate reads from disk.** Its input is a set of file paths and plain
   config — never the objects the producing station held in memory. That is
   the mechanical version of "never let the same step both produce and
   approve an artifact": a gate physically cannot see the generator's intent,
   only the artifact a buyer would get.
2. **A failing check fails the build.** A gate that only ever produces advice
   is decoration. ``GateReport.enforce()`` raises, and the pipeline stops.

Every run appends one line per gate to a telemetry file so you can answer the
question the pack asks: how often does this gate actually fail something?
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..config import EngineConfig
from ..errors import GateFailure


@dataclass(frozen=True)
class Check:
    """One criterion, scored PASS or FAIL with a one-line reason."""

    name: str
    passed: bool
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    advisory: bool = False  # advisory checks report but never fail the build

    @property
    def verdict(self) -> str:
        if self.passed:
            return "PASS"
        return "WARN" if self.advisory else "FAIL"

    def line(self) -> str:
        return f"{self.verdict:<4}  {self.name:<22}  {self.reason}"


@dataclass
class GateReport:
    """The verdict of one gate over one subject."""

    gate_id: str
    title: str
    subject: str
    checks: list[Check] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking_failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and not c.advisory]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.advisory]

    @property
    def passed(self) -> bool:
        return not self.blocking_failures

    def add(
        self,
        name: str,
        passed: bool,
        reason: str,
        evidence: dict[str, Any] | None = None,
        advisory: bool = False,
    ) -> Check:
        check = Check(name, passed, reason, evidence or {}, advisory)
        self.checks.append(check)
        return check

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "title": self.title,
            "subject": self.subject,
            "created_at": self.created_at,
            "passed": self.passed,
            "failed_checks": [c.name for c in self.blocking_failures],
            "warnings": [c.name for c in self.warnings],
            "checks": [asdict(c) | {"verdict": c.verdict} for c in self.checks],
            "metrics": self.metrics,
        }

    def to_text(self) -> str:
        head = f"{self.gate_id.upper()} — {self.title}\nsubject: {self.subject}"
        body = "\n".join("  " + c.line() for c in self.checks)
        verdict = "GATE PASSED" if self.passed else "GATE FAILED — build stopped"
        if self.passed and self.warnings:
            verdict += f" ({len(self.warnings)} warning(s))"
        return f"{head}\n{body}\n  → {verdict}"

    def enforce(self) -> "GateReport":
        """Raise ``GateFailure`` if any blocking check failed."""
        if not self.passed:
            raise GateFailure(self)
        return self


@dataclass(frozen=True)
class GateInput:
    """What a gate is allowed to know: paths on disk, and plain facts.

    Deliberately not "the book object". If a gate needs something, it opens
    the file a buyer would open.
    """

    subject: str
    paths: dict[str, Path]
    facts: dict[str, Any] = field(default_factory=dict)

    def path(self, role: str) -> Path:
        try:
            path = self.paths[role]
        except KeyError:
            raise GateFailure(
                GateReport(
                    gate_id="input",
                    title="gate input",
                    subject=self.subject,
                    checks=[
                        Check(
                            "artifact_present",
                            False,
                            f"the gate needs artifact {role!r} and it was never produced",
                        )
                    ],
                )
            ) from None
        if not path.is_file():
            raise GateFailure(
                GateReport(
                    gate_id="input",
                    title="gate input",
                    subject=self.subject,
                    checks=[
                        Check(
                            "artifact_present",
                            False,
                            f"artifact {role!r} is recorded but missing on disk: {path}",
                        )
                    ],
                )
            )
        return path

    def json(self, role: str) -> Any:
        return json.loads(self.path(role).read_text(encoding="utf-8"))


class Gate:
    """Base class. Subclasses implement ``evaluate``; the rest is plumbing."""

    gate_id: str = "gate"
    title: str = "unnamed gate"
    requires: tuple[str, ...] = ()

    def __init__(self, config: EngineConfig | None = None) -> None:
        self.config = config or EngineConfig()

    def evaluate(self, data: GateInput, report: GateReport) -> None:  # pragma: no cover
        raise NotImplementedError

    def run(self, data: GateInput) -> GateReport:
        report = GateReport(self.gate_id, self.title, data.subject)
        for role in self.requires:
            data.path(role)  # raises a GateFailure naming the missing artifact
        self.evaluate(data, report)
        if not report.checks:
            report.add(
                "gate_has_criteria",
                False,
                "this gate produced no checks at all — it cannot fail anything",
            )
        return report


def record_gate_result(
    config: EngineConfig, report: GateReport, run_slug: str
) -> Path | None:
    """Append one line of gate telemetry.

    "Never failing is as bad a sign as always failing." You cannot know which
    one you have without keeping count.
    """
    path = Path(config.telemetry_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "at": report.created_at,
                "run": run_slug,
                "gate_id": report.gate_id,
                "passed": report.passed,
                "failed_checks": [c.name for c in report.blocking_failures],
                "warnings": [c.name for c in report.warnings],
                "check_count": len(report.checks),
            },
            ensure_ascii=False,
        )
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return path
    except OSError:
        # Telemetry is never allowed to break a build.
        return None


def summarize_telemetry(path: Path) -> dict[str, dict[str, Any]]:
    """Fail rate per gate, plus the verdict on the gate itself."""
    stats: dict[str, dict[str, Any]] = {}
    if not Path(path).is_file():
        return stats
    with Path(path).open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            entry = stats.setdefault(
                row.get("gate_id", "?"),
                {"runs": 0, "failures": 0, "failed_checks": {}, "fail_rate": 0.0},
            )
            entry["runs"] += 1
            if not row.get("passed", True):
                entry["failures"] += 1
                for name in row.get("failed_checks", []):
                    entry["failed_checks"][name] = entry["failed_checks"].get(name, 0) + 1
    for entry in stats.values():
        entry["fail_rate"] = round(entry["failures"] / entry["runs"], 3) if entry["runs"] else 0.0
        entry["verdict"] = _telemetry_verdict(entry["runs"], entry["fail_rate"])
    return stats


def _telemetry_verdict(runs: int, fail_rate: float) -> str:
    if runs < 5:
        return "not enough runs to judge this gate yet"
    if fail_rate == 0.0:
        return "never fails — suspect the criteria are too vague to catch anything"
    if fail_rate >= 0.9:
        return "almost always fails — the bar or the generator is miscalibrated"
    return "healthy: this gate has caught things and let things through"


def checks_from(rows: Iterable[tuple[str, bool, str]]) -> list[Check]:
    return [Check(name, passed, reason) for name, passed, reason in rows]
