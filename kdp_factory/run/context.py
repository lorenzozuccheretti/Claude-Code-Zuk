"""The build context — the only object that knows where files go.

Stations receive a context, write real files through it, and record what they
wrote. Nothing in the engine writes to a path it invented itself.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .. import __version__
from ..config import EngineConfig
from ..content.rng import StageRandom, derive_seed
from ..errors import GateFailure
from ..naming import GATES_DIR, STATION_DIRS, run_slug
from ..spec.kdp import KDP_SPEC
from .manifest import Artifact, Manifest

log = logging.getLogger("kdp_factory")


class BuildContext:
    """One run of the engine, from niche file to upload plan."""

    def __init__(
        self,
        niche_slug: str,
        book_type: str,
        seed: int,
        config: EngineConfig,
        output_root: Path | None = None,
    ) -> None:
        self.config = config
        self.niche_slug = niche_slug
        self.book_type = book_type
        self.seed = seed
        self.slug = run_slug(niche_slug, book_type, seed)
        self.run_seed = derive_seed(niche_slug, book_type, seed)
        self.root = Path(output_root or config.output_root) / self.slug
        self.manifest = Manifest(
            slug=self.slug,
            niche_slug=niche_slug,
            book_type=book_type,
            seed=seed,
            engine_version=__version__,
            spec_version=str(KDP_SPEC["spec_version"]),
        )
        self._ensure_dirs()

    # ---------------------------------------------------------------- paths
    def _ensure_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for name in STATION_DIRS.values():
            (self.root / name).mkdir(exist_ok=True)
        (self.root / GATES_DIR).mkdir(exist_ok=True)

    def station_dir(self, station: int) -> Path:
        try:
            return self.root / STATION_DIRS[station]
        except KeyError:
            raise ValueError(f"no such station: {station}") from None

    @property
    def gates_dir(self) -> Path:
        return self.root / GATES_DIR

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    # ------------------------------------------------------------- writing
    def write_bytes(self, station: int, filename: str, data: bytes, role: str) -> Path:
        path = self.station_dir(station) / filename
        path.write_bytes(data)
        self.register(role, path, station)
        return path

    def write_text(self, station: int, filename: str, text: str, role: str) -> Path:
        path = self.station_dir(station) / filename
        path.write_text(text, encoding="utf-8")
        self.register(role, path, station)
        return path

    def write_json(self, station: int, filename: str, data: Any, role: str) -> Path:
        payload = json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n"
        return self.write_text(station, filename, payload, role)

    def write_gate_report(self, gate_id: str, data: Any) -> Path:
        """Gate verdicts live in their own folder, not inside a station's."""
        path = self.gates_dir / f"{gate_id}.json"
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        self.register(f"gate_{gate_id}", path, station=0)
        return path

    def register(self, role: str, path: Path, station: int) -> Artifact:
        """Record a file that already exists, with its size and hash."""
        artifact = Artifact.from_path(role, path, station, self.root)
        self.manifest.add_artifact(artifact)
        return artifact

    def path_of(self, role: str) -> Path:
        """Resolve a recorded artifact back to an absolute path."""
        artifact = self.manifest.artifact(role)
        if artifact is None:
            raise KeyError(f"no artifact recorded under role {role!r}")
        return self.root / artifact.path

    def read_json(self, role: str) -> Any:
        return json.loads(self.path_of(role).read_text(encoding="utf-8"))

    # ------------------------------------------------------------ metadata
    def fact(self, key: str, value: Any) -> Any:
        """Record a fact later stations (and gates) are allowed to rely on."""
        self.manifest.facts[key] = value
        return value

    def rng(self, stage: str) -> StageRandom:
        return StageRandom(self.run_seed, stage)

    def save(self) -> Path:
        return self.manifest.write(self.manifest_path)

    # -------------------------------------------------------------- stages
    @contextmanager
    def stage(self, station: int, name: str) -> Iterator[Any]:
        record = self.manifest.stage(station, name)
        record.status = "running"
        record.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        started = time.perf_counter()
        log.info("station %d — %s", station, name)
        try:
            yield record
        except GateFailure as exc:
            record.status = "failed"
            record.error = str(exc)
            self.manifest.status = "gate_failed"
            raise
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
            record.status = "failed"
            record.error = f"{type(exc).__name__}: {exc}"
            self.manifest.status = "error"
            raise
        else:
            record.status = "ok"
        finally:
            record.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            record.duration_s = round(time.perf_counter() - started, 3)
            self.save()
