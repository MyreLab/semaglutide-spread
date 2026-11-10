"""Draw the US epidemic curve of semaglutide adoption, for the README.

Two panels, side by side, because they measure different things on different scales (one
chart with two y-axes would invite false comparisons):
- left: new adopters each year (the classic epidemic curve: new cases per period);
- right: the share of each year's population at risk that has ever adopted (prevalence).

Each is drawn twice, for GitHub's light and dark themes; the README shows the one that matches
the reader's theme.

Inputs:  data/warehouse.duckdb (table `rpt_us_adoption_curve`)
Outputs: docs/images/us_adoption_curve_light.png and docs/images/us_adoption_curve_dark.png

Usage:
    uv run adoption-curve
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from semaglutide_spread.paths import WAREHOUSE_PATH
from semaglutide_spread.viz.night_lights import IMAGES_DIR

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Theme:
    name: str
    surface: str  # matches GitHub's page background, so the chart sits flush
    text: str
    secondary: str
    muted: str
    grid: str
    baseline: str
    series: str  # one series per panel: blue, validated for contrast on this surface


LIGHT = Theme("light", "#ffffff", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#2a78d6")
DARK = Theme("dark", "#0d1117", "#f0efec", "#c3c2b7", "#898781", "#2c2c2a", "#383835", "#3987e5")


@dataclass
class Curve:
    years: list[int]
    new_adopters: list[int]
    prevalence_pct: list[float]


def load(con: duckdb.DuckDBPyConnection) -> Curve:
    rows = con.execute(
        "select year, new_adopters, prevalence_pct from rpt_us_adoption_curve order by year"
    ).fetchall()
    return Curve([r[0] for r in rows], [r[1] for r in rows], [float(r[2]) for r in rows])


def style_axes(ax, theme: Theme) -> None:
    """Recessive axes: hairline horizontal grid, no box, muted tick labels."""
    ax.set_facecolor(theme.surface)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(theme.baseline)
    ax.grid(axis="y", color=theme.grid, linewidth=1, linestyle="-")
    ax.set_axisbelow(True)
    ax.tick_params(colors=theme.muted, labelsize=10, length=0, pad=6)


def draw(curve: Curve, theme: Theme, path: Path) -> Path:
    # A Figure made directly (not through pyplot) draws to files without opening a window.
    fig = Figure(figsize=(10, 4.2), dpi=200)
    left, right = fig.subplots(1, 2)
    fig.patch.set_facecolor(theme.surface)
    for ax in (left, right):
        style_axes(ax, theme)
        ax.set_xticks(curve.years)

    # Left: new adopters each year, value on each column.
    left.bar(curve.years, curve.new_adopters, width=0.45, color=theme.series)
    left.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1000:.0f}k" if v else "0"))
    for year, n in zip(curve.years, curve.new_adopters, strict=True):
        left.annotate(
            f"{n:,}",
            (year, n),
            xytext=(0, 4),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            color=theme.secondary,
        )
    left.set_ylim(0, max(curve.new_adopters) * 1.15)
    left.set_title("New adopters each year", loc="left", fontsize=12, color=theme.text, pad=12)

    # Right: share of the population at risk that has ever adopted, labelled at the end.
    right.plot(
        curve.years,
        curve.prevalence_pct,
        color=theme.series,
        linewidth=2,
        marker="o",
        markersize=5,
        markeredgecolor=theme.surface,
        markeredgewidth=1.5,
        solid_joinstyle="round",
        solid_capstyle="round",
    )
    right.set_ylim(0, 100)
    right.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    right.annotate(
        f"{curve.prevalence_pct[-1]:.0f}%",
        (curve.years[-1], curve.prevalence_pct[-1]),
        xytext=(0, 9),
        textcoords="offset points",
        ha="center",
        fontsize=10,
        color=theme.text,
        fontweight="bold",
    )
    right.set_title(
        "Share of prescribers who have adopted",
        loc="left",
        fontsize=12,
        color=theme.text,
        pad=12,
    )

    fig.text(
        0.01,
        0.01,
        "Medicare Part D prescribers. Adoption: first year with 11+ semaglutide claims. "
        "Share: of each year's prescribers of any glucose-lowering drug. Source: CMS.",
        fontsize=8,
        color=theme.muted,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1), w_pad=3)
    fig.savefig(path, facecolor=theme.surface)
    return path


def render(con: duckdb.DuckDBPyConnection, out_dir: Path = IMAGES_DIR) -> list[Path]:
    curve = load(con)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [draw(curve, t, out_dir / f"us_adoption_curve_{t.name}.png") for t in (LIGHT, DARK)]
    log.info("Wrote %s", ", ".join(p.name for p in paths))
    return paths


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.split("\n")[0]).parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with duckdb.connect(str(WAREHOUSE_PATH), read_only=True) as con:
        render(con)
    return 0


if __name__ == "__main__":
    sys.exit(main())
