"""Failure modes the engine is allowed to have.

A gate failure is not an exception in the "something went wrong" sense: it is
the engine working correctly. It stops the build and says why.
"""

from __future__ import annotations


class KdpFactoryError(Exception):
    """Base class for every error this engine raises on purpose."""


class ConfigError(KdpFactoryError):
    """The inputs (niche file, brand config, book type) are not usable."""


class SpecViolation(KdpFactoryError):
    """A computed value falls outside the KDP spec card (trim, pages, spine)."""


class GateFailure(KdpFactoryError):
    """A gate failed the build. Carries the report so callers can print it."""

    def __init__(self, report):
        self.report = report
        failed = [c for c in report.checks if not c.passed and not c.advisory]
        detail = "; ".join(f"{c.name}: {c.reason}" for c in failed) or "no reason recorded"
        super().__init__(f"GATE FAILED: {report.gate_id} — {detail}")


class RenderError(KdpFactoryError):
    """A renderer could not produce the artifact it promised."""
