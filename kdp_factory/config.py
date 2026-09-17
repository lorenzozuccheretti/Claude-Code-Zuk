"""Brand and engine configuration.

Two objects, deliberately separate:

* ``Brand``  — what every book in the imprint has in common (name, fonts,
  palette, copyright, back-matter boilerplate). Answering the interview
  questions from Prompt 1 means filling this in once.
* ``EngineConfig`` — how the factory behaves: where output goes, how strict
  the gates are, whether the optional LLM hook is on.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError

DEFAULT_OUTPUT_ROOT = Path("output")

# Type 1 fonts every ReportLab install ships with. Using them keeps a build
# reproducible on a machine that has no fonts installed at all; register real
# TTFs through Brand.fonts when you want the imprint to look like itself.
BUILTIN_FONTS = {
    "serif": {"regular": "Times-Roman", "bold": "Times-Bold", "italic": "Times-Italic"},
    "sans": {"regular": "Helvetica", "bold": "Helvetica-Bold", "italic": "Helvetica-Oblique"},
    "mono": {"regular": "Courier", "bold": "Courier-Bold", "italic": "Courier-Oblique"},
}


@dataclass(frozen=True)
class Fonts:
    """Font roles, not font files — renderers ask for a role."""

    body: str = "serif"
    display: str = "sans"
    accent: str = "sans"
    ttf: dict[str, str] = field(default_factory=dict)

    def resolve(self, role: str, weight: str = "regular") -> str:
        family = getattr(self, role, None) or role
        if family in self.ttf:
            return family
        if family not in BUILTIN_FONTS:
            raise ConfigError(
                f"font family {family!r} is neither a builtin "
                f"({', '.join(BUILTIN_FONTS)}) nor a registered TTF"
            )
        return BUILTIN_FONTS[family][weight]


@dataclass(frozen=True)
class Brand:
    """The imprint. One of these, many books."""

    imprint: str = "Quiet Press"
    author: str = "Quiet Press"
    website: str = ""
    tagline: str = ""
    copyright_holder: str = ""
    copyright_year: int = 2026
    language: str = "english"
    fonts: Fonts = field(default_factory=Fonts)
    # Pin the imprint's look, or leave empty and let each niche choose its own.
    # Keys come from kdp_factory/render/design.py (PALETTES, MOTIFS).
    palette_key: str = ""
    motif: str = ""
    also_by: list[str] = field(default_factory=list)
    back_matter_note: str = ""
    default_trim: str = "6x9"
    default_paper: str = "bw_white"

    def __post_init__(self) -> None:
        if not self.imprint.strip():
            raise ConfigError("brand.imprint must not be empty")
        object.__setattr__(
            self, "copyright_holder", self.copyright_holder or self.author
        )


@dataclass(frozen=True)
class QualityBar:
    """The numbers behind "would a buyer feel they got their money's worth".

    These are the thresholds the substance gate applies. Raising them makes the
    gate stricter; every one of them can fail a build.
    """

    max_filler_page_ratio: float = 0.15
    min_unique_content_ratio: float = 0.97
    max_pairwise_similarity: float = 0.70
    # Value is measured as the share of pages that carry content, not as units
    # per page: a planner spends two pages on one week and a puzzle book spends
    # a page on a puzzle and half a page on its answer. Both are honest.
    min_content_page_ratio: float = 0.80
    min_chars_per_content_page: int = 20
    min_front_matter_pages: int = 2
    min_back_matter_pages: int = 1
    require_page_numbers: bool = True
    # The engine ships real typefaces; a book set in Helvetica means the vendored
    # fonts did not load, which is a defect worth stopping for.
    require_vendored_fonts: bool = True
    # A cover is first seen at about 200px tall in a grid of twenty. Below these
    # the title is decoration rather than a title.
    min_title_cap_px: float = 6.5
    min_title_contrast: float = 4.5
    forbidden_tokens: list[str] = field(
        default_factory=lambda: [
            "lorem ipsum", "todo", "tbd", "{{", "}}", "<placeholder>",
            "xxx", "insert here", "none none",
        ]
    )


@dataclass(frozen=True)
class NicheGateConfig:
    """When a niche is allowed through to the presses."""

    min_score: float = 6.0
    min_demand_signal: float = 5.0
    max_trademark_risk: float = 3.0
    require_evidence_for: list[str] = field(
        default_factory=lambda: ["demand_volume", "proven_sales", "differentiation"]
    )


@dataclass(frozen=True)
class PricePolicy:
    """How the list price is derived. Every number here is used by code.

    The floor is never a matter of taste: below ``printing cost / royalty rate``
    the book loses money on every copy, so the engine refuses it.
    """

    markup_over_cost: float = 1.45      # multiple of the royalty-floor price
    round_to: float = 0.99              # land on x.99
    min_royalty_usd: float = 1.50       # per copy, at the standard rate
    undercut_competitor_by: float = 0.0 # e.g. 0.05 to price 5% under the incumbent
    max_price_usd: float | None = None


@dataclass(frozen=True)
class LLMConfig:
    """The optional hook. Off by default, so builds stay reproducible."""

    enabled: bool = False
    provider: str = "null"
    model: str = "claude-sonnet-5"
    max_tokens: int = 2000
    temperature: float = 0.0
    api_key_env: str = "ANTHROPIC_API_KEY"
    # An LLM may advise the substance gate, but only code can fail the build,
    # unless you knowingly flip this.
    allow_llm_to_fail_gate: bool = False


@dataclass(frozen=True)
class EngineConfig:
    """Everything the factory needs that is not the niche itself."""

    brand: Brand = field(default_factory=Brand)
    quality: QualityBar = field(default_factory=QualityBar)
    niche_gate: NicheGateConfig = field(default_factory=NicheGateConfig)
    price: PricePolicy = field(default_factory=PricePolicy)
    llm: LLMConfig = field(default_factory=LLMConfig)
    output_root: Path = DEFAULT_OUTPUT_ROOT
    telemetry_path: Path = Path("output/_telemetry/gates.jsonl")
    config_path: Path | None = None

    @staticmethod
    def _derived_telemetry(root: Path) -> Path:
        return Path(root) / "_telemetry" / "gates.jsonl"

    def with_output_root(self, root: str | Path) -> "EngineConfig":
        """Move the output root — and the telemetry with it, unless it was pinned.

        Without this, `--output somewhere/else` would still append gate results
        to the default folder, and `kdp gates` would report on the wrong runs.
        """
        root = Path(root)
        pinned = self.telemetry_path != self._derived_telemetry(self.output_root)
        return replace(
            self,
            output_root=root,
            telemetry_path=self.telemetry_path if pinned else self._derived_telemetry(root),
        )

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["output_root"] = str(self.output_root)
        data["telemetry_path"] = str(self.telemetry_path)
        data["config_path"] = str(self.config_path) if self.config_path else None
        return data


def _build(cls, data: Any, name: str):
    if data is None:
        return cls()
    if not isinstance(data, dict):
        raise ConfigError(f"config section {name!r} must be a mapping, got {type(data).__name__}")
    known = {f.name for f in cls.__dataclass_fields__.values()}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(
            f"unknown key(s) in {name!r}: {', '.join(sorted(unknown))}; "
            f"known keys: {', '.join(sorted(known))}"
        )
    return cls(**data)


def load_config(path: str | Path | None = None) -> EngineConfig:
    """Load engine config from YAML. With no path, return the defaults.

    Also honours ``KDP_FACTORY_CONFIG`` so a scheduled run can point at a
    different imprint without changing the command line.
    """
    if path is None:
        env_path = os.environ.get("KDP_FACTORY_CONFIG")
        if not env_path:
            return EngineConfig()
        path = env_path

    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path} must contain a YAML mapping")

    brand_raw = dict(raw.get("brand") or {})
    fonts = _build(Fonts, brand_raw.pop("fonts", None), "brand.fonts")
    if "palette" in brand_raw:
        raise ConfigError(
            "brand.palette held raw hex values and has been replaced by "
            "brand.palette_key, which names one of the engine's colour worlds "
            "(see kdp_factory/render/design.py). Set brand.palette_key: dusk "
            "— or drop the block and let each niche pick its own."
        )
    brand = _build(Brand, brand_raw, "brand")
    brand = replace(brand, fonts=fonts)

    output_root = Path(raw.get("output_root") or DEFAULT_OUTPUT_ROOT)
    telemetry = raw.get("telemetry_path")
    return EngineConfig(
        brand=brand,
        quality=_build(QualityBar, raw.get("quality"), "quality"),
        niche_gate=_build(NicheGateConfig, raw.get("niche_gate"), "niche_gate"),
        price=_build(PricePolicy, raw.get("price"), "price"),
        llm=_build(LLMConfig, raw.get("llm"), "llm"),
        output_root=output_root,
        telemetry_path=Path(telemetry) if telemetry else output_root / "_telemetry" / "gates.jsonl",
        config_path=config_path,
    )
