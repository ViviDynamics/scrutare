"""Dispatch supported review strategies before touching capture or runtime state."""

from pathlib import Path

from scrutare.config import ReviewConfig
from scrutare.engine.panel import PanelResult, run_panel
from scrutare.engine.session_models import NareRuntime


class StrategyNotImplementedError(NotImplementedError):
    """The selected strategy has no execution implementation in this release."""


async def run_review(run_dir: Path, config: ReviewConfig, *, runtime: NareRuntime) -> PanelResult:
    """Run the independent panel strategy without inventing later milestone behavior."""
    if config.strategy != "panel":
        raise StrategyNotImplementedError(f"Strategy {config.strategy!r} is not yet implemented.")
    return await run_panel(run_dir, config, runtime=runtime)
