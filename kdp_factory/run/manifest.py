"""The run manifest: what was produced, by whom, and what it hashes to.

The manifest is how a later station learns about an earlier one. Stations do
not pass Python objects to each other in memory — they write files and record
them here, so any stage can be re-run, inspected or graded on its own.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..naming import sha256_file


@dataclass
class Artifact:
    """One real file on disk, with its hash."""

    role: str
    path: str
    bytes: int
    sha256: str
    station: int

    @classmethod
    def from_path(cls, role: str, path: Path, station: int, root: Path) -> "Artifact":
        return cls(
            role=role,
            path=str(path.relative_to(root)),
            bytes=path.stat().st_size,
            sha256=sha256_file(path),
            station=station,
        )


@dataclass
class StageRecord:
    """What one station did."""

    station: int
    name: str
    status: str = "pending"  # pending | ok | failed | skipped
    started_at: str | None = None
    finished_at: str | None = None
    duration_s: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class Manifest:
    """Everything a run knows about itself."""

    slug: str
    niche_slug: str
    book_type: str
    seed: int
    engine_version: str
    spec_version: str
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    status: str = "running"  # running | complete | gate_failed | error
    stages: list[StageRecord] = field(default_factory=list)
    gates: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    def stage(self, station: int, name: str) -> StageRecord:
        for record in self.stages:
            if record.station == station:
                return record
        record = StageRecord(station=station, name=name)
        self.stages.append(record)
        self.stages.sort(key=lambda r: r.station)
        return record

    def add_artifact(self, artifact: Artifact) -> Artifact:
        self.artifacts = [a for a in self.artifacts if a.role != artifact.role]
        self.artifacts.append(artifact)
        self.artifacts.sort(key=lambda a: (a.station, a.role))
        return artifact

    def artifact(self, role: str) -> Artifact | None:
        for artifact in self.artifacts:
            if artifact.role == role:
                return artifact
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "niche_slug": self.niche_slug,
            "book_type": self.book_type,
            "seed": self.seed,
            "engine_version": self.engine_version,
            "spec_version": self.spec_version,
            "created_at": self.created_at,
            "status": self.status,
            "stages": [asdict(s) for s in self.stages],
            "gates": self.gates,
            "artifacts": [asdict(a) for a in self.artifacts],
            "facts": self.facts,
        }

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.as_dict(), indent=2, ensure_ascii=False, sort_keys=False)
            + "\n",
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        manifest = cls(
            slug=data["slug"],
            niche_slug=data["niche_slug"],
            book_type=data["book_type"],
            seed=data["seed"],
            engine_version=data["engine_version"],
            spec_version=data["spec_version"],
            created_at=data["created_at"],
            status=data["status"],
            facts=data.get("facts", {}),
            gates=data.get("gates", []),
        )
        manifest.stages = [StageRecord(**s) for s in data.get("stages", [])]
        manifest.artifacts = [Artifact(**a) for a in data.get("artifacts", [])]
        return manifest
