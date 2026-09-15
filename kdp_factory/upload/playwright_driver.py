"""The browser half of station 5.

It opens KDP, types what the upload plan says, reads the form back, and stops.
Three deliberate limits:

* **It does not log in.** The window opens, you sign in, it continues. No
  credential ever passes through this engine.
* **It reads back what it typed** and reports any field whose value on screen
  does not match the plan, before you touch anything.
* **It never clicks Publish.** ``FORBIDDEN_ACTIONS`` is checked before every
  click, and the run ends on the review screen with the book unpublished.

Playwright is an optional dependency: ``pip install 'kdp-factory[upload]'``.
Without it, ``--dry-run`` (the default) still prints the whole run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .plan import FORBIDDEN_ACTIONS

KDP_BOOKSHELF = "https://kdp.amazon.com/en_US/bookshelf"


class PublishAttempted(RuntimeError):
    """Raised if anything in this process ever aims at the Publish button."""


def assert_not_forbidden(action: str) -> None:
    lowered = action.lower()
    for forbidden in FORBIDDEN_ACTIONS:
        if forbidden in lowered:
            raise PublishAttempted(
                f"refusing to perform {action!r}: this engine stops before "
                f"{forbidden!r}. A human publishes."
            )


@dataclass
class FieldOutcome:
    name: str
    screen: str
    status: str  # filled | not_found | mismatch | skipped
    strategy: str = ""
    detail: str = ""

    def line(self) -> str:
        mark = {"filled": "ok  ", "not_found": "MISS", "mismatch": "DIFF", "skipped": "skip"}
        return f"{mark.get(self.status, '?   ')} {self.screen}.{self.name} {self.detail}".rstrip()


class UploadRun:
    """Executes an upload plan. Dry by default."""

    def __init__(self, plan: dict[str, Any], dry_run: bool = True, timeout_ms: int = 15000):
        self.plan = plan
        self.dry_run = dry_run
        self.timeout_ms = timeout_ms
        self.outcomes: list[FieldOutcome] = []

    # ------------------------------------------------------------ dry mode
    def describe(self) -> str:
        lines = [
            f"UPLOAD RUN (dry) — {self.plan['slug']}",
            f"marketplace: {self.plan['marketplace']}",
            f"stops before: {self.plan['stops_before']}",
            "",
            "would type:",
        ]
        for item in self.plan["fields"]:
            value = str(item["value"])
            shown = value if len(value) <= 70 else value[:67] + "…"
            lines.append(f"  {item['screen']:<8} {item['name']:<18} = {shown}")
        lines.append("")
        lines.append("would upload:")
        for item in self.plan["files"]:
            path = Path(item["value"])
            exists = "exists" if path.is_file() else "MISSING"
            lines.append(f"  {item['name']:<12} {path}  [{exists}]")
        lines += ["", "would then read back: " + ", ".join(self.plan["read_back"])]
        lines += ["", "notes:"] + [f"  - {n}" for n in self.plan["notes"]]
        lines += ["", f"and stop. Forbidden actions: {', '.join(FORBIDDEN_ACTIONS)}."]
        return "\n".join(lines)

    # --------------------------------------------------------- browser mode
    def execute(self, user_data_dir: str | Path | None = None) -> list[FieldOutcome]:
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "playwright is not installed. Install it with "
                "`pip install 'kdp-factory[upload]' && playwright install chromium`, "
                "or run with --dry-run."
            ) from exc

        missing = [f["value"] for f in self.plan["files"] if not Path(f["value"]).is_file()]
        if missing:
            raise FileNotFoundError(
                "these files are in the plan but not on disk: " + ", ".join(missing)
            )

        with sync_playwright() as p:
            if user_data_dir:
                context = p.chromium.launch_persistent_context(
                    str(user_data_dir), headless=False
                )
                page = context.pages[0] if context.pages else context.new_page()
            else:
                browser = p.chromium.launch(headless=False)
                context = browser.new_context()
                page = context.new_page()

            page.set_default_timeout(self.timeout_ms)
            page.goto(KDP_BOOKSHELF)
            print(
                "\nSign in to KDP in the window that just opened, then open the paperback\n"
                "you are creating and press Enter here to let the run continue.\n"
            )
            input("[enter to continue] ")

            for item in self.plan["fields"]:
                self.outcomes.append(self._fill(page, item))
            for item in self.plan["files"]:
                self.outcomes.append(self._upload(page, item))
            self._read_back(page)

            print("\n".join(o.line() for o in self.outcomes))
            print(
                "\nSTOPPED BEFORE PUBLISH. The browser stays open: review every screen, "
                "then publish by hand if you are happy.\n"
            )
            input("[enter to close the browser] ")
            context.close()
        return self.outcomes

    # -------------------------------------------------------------- filling
    def _locate(self, page, item: dict[str, Any]):
        for label in item.get("labels", []):
            try:
                locator = page.get_by_label(label, exact=False).first
                if locator.count() > 0:
                    return locator, f"label:{label}"
            except Exception:  # noqa: BLE001 - a failed strategy is not an error
                continue
        for selector in item.get("css", []):
            try:
                locator = page.locator(selector).first
                if locator.count() > 0:
                    return locator, f"css:{selector}"
            except Exception:  # noqa: BLE001
                continue
        return None, ""

    def _fill(self, page, item: dict[str, Any]) -> FieldOutcome:
        assert_not_forbidden(item["name"])
        locator, strategy = self._locate(page, item)
        if locator is None:
            return FieldOutcome(
                item["name"], item["screen"], "not_found",
                detail="no label or selector matched — fix selectors.yaml, do not guess",
            )
        value = str(item["value"])
        try:
            if item["kind"] == "select":
                locator.select_option(label=value)
            elif item["kind"] == "radio":
                locator.first.check()
            else:
                locator.fill(value)
        except Exception as exc:  # noqa: BLE001
            return FieldOutcome(
                item["name"], item["screen"], "not_found", strategy, f"{type(exc).__name__}: {exc}"
            )
        return FieldOutcome(item["name"], item["screen"], "filled", strategy)

    def _upload(self, page, item: dict[str, Any]) -> FieldOutcome:
        locator, strategy = self._locate(page, item)
        if locator is None:
            return FieldOutcome(item["name"], item["screen"], "not_found", detail="upload input not found")
        try:
            locator.set_input_files(item["value"])
        except Exception as exc:  # noqa: BLE001
            return FieldOutcome(item["name"], item["screen"], "not_found", strategy, str(exc))
        return FieldOutcome(item["name"], item["screen"], "filled", strategy, Path(item["value"]).name)

    def _read_back(self, page) -> None:
        """Compare what is on screen with what the plan said. Report differences."""
        by_name = {f["name"]: f for f in self.plan["fields"]}
        for name in self.plan["read_back"]:
            item = by_name.get(name)
            if item is None:
                continue
            locator, strategy = self._locate(page, item)
            if locator is None:
                self.outcomes.append(
                    FieldOutcome(name, item["screen"], "not_found", detail="could not re-read")
                )
                continue
            try:
                on_screen = locator.input_value()
            except Exception:  # noqa: BLE001
                on_screen = (locator.text_content() or "").strip()
            expected = str(item["value"]).strip()
            if on_screen.strip() != expected:
                self.outcomes.append(
                    FieldOutcome(
                        name, item["screen"], "mismatch", strategy,
                        f"form says {on_screen[:40]!r}, plan says {expected[:40]!r}",
                    )
                )


def run_from_file(
    plan_path: str | Path, dry_run: bool = True, user_data_dir: str | Path | None = None
) -> UploadRun:
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    run = UploadRun(plan, dry_run=dry_run)
    if dry_run:
        print(run.describe())
    else:
        run.execute(user_data_dir=user_data_dir)
    return run
