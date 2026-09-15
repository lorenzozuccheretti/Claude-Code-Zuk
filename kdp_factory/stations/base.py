"""What every station has in common.

A station takes the build context, writes real files into its own numbered
folder, records facts other stations may rely on — and never grades its own
work. Grading is what the gates between stations are for.
"""

from __future__ import annotations

from typing import Any

from ..config import EngineConfig
from ..run.context import BuildContext


class Station:
    number: int = 0
    name: str = "station"

    def __init__(self, config: EngineConfig | None = None) -> None:
        self.config = config or EngineConfig()

    def run(self, ctx: BuildContext, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    def __call__(self, ctx: BuildContext, **kwargs: Any) -> Any:
        with ctx.stage(self.number, self.name):
            return self.run(ctx, **kwargs)
