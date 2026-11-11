"""Draw the US vs UK comparison chart, for the README: how fast semaglutide spread, and how
much of it is prescribed.

Two panels, side by side (different measures, so two charts rather than one with two y-axes):
- left: the share of prescribers (US) or GP practices (UK) that have adopted, against years
  since the first adopters. Aligning the start puts both curves on one time axis, so their
  shapes can be compared even though the UK started a year later. Both use the 11+ rule.
- right: semaglutide's share of glucose-lowering prescriptions, by calendar year: US Medicare
  in 30-day fills, UK GP practices in items (complete years only).

The two countries count different units (a practice is many prescribers; Medicare is mostly
people 65 and over), so the chart compares shape and timing; the README says so beside it.

Inputs:  data/warehouse.duckdb (`rpt_us_adoption_curve`, `rpt_uk_adoption_curve`,
         `rpt_us_prescription_share`, `rpt_uk_prescription_share`)
Outputs: docs/images/us_uk_comparison_{light,dark}.png

Usage:
    uv run comparison-chart
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MultipleLocator

from semaglutide_spread.paths import WAREHOUSE_PATH
from semaglutide_spread.viz.adoption_curve import DARK, LIGHT, Theme, percent_axis, style_axes
from semaglutide_spread.viz.night_lights import IMAGES_DIR

log = logging.getLogger(__name__)

# The second series' colour (the UK), the next categorical slot after blue, per theme.
# Checked with the dataviz palette validator against each theme's background.
UK_COLOUR = {"light": "#eb6834", "dark": "#d95926"}


@dataclass
class Series:
    x: list[float]
    y: list[float]


@dataclass
class Comparison:
    us_adoption: Series  # x: years since the first adopters
    uk_adoption: Series
    us_share: Series  # x: calendar year
    uk_share: Series
    us_start: int  # first year with adopters
    uk_start: str  # first month with adopters, e.g. "Jan 2019"


def load(con: duckdb.DuckDBPyConnection) -> Comparison:
    # US: yearly prevalence; year-end values sit at 1, 2, ... years after the start.
    us = con.execute(
        "select year, prevalence_pct from rpt_us_adoption_curve order by year"
    ).fetchall()
    us_start = min(year for year, pct in us if pct > 0)
    us_adoption = Series(
        [0.0] + [year - us_start + 1 for year, _ in us if year >= us_start],
        [0.0] + [float(pct) for year, pct in us if year >= us_start],
    )

    # UK: monthly prevalence (matched 11+ measure), all nations, to the last month all have.
    uk = con.execute(
        """
        with last as (
            select min(last_month) as month
            from (select max(month) as last_month from rpt_uk_adoption_curve group by nation)
        )
        select month, 100.0 * sum(matched_adopted_at_risk) / sum(at_risk)
        from rpt_uk_adoption_curve
        where month <= (select month from last)
        group by month
        order by month
        """
    ).fetchall()
    start = next(month for month, pct in uk if pct > 0)
    months_since = [(m.year - start.year) * 12 + m.month - start.month + 1 for m, _ in uk]
    uk_adoption = Series(
        [0.0] + [n / 12 for n in months_since if n >= 1],
        [0.0] + [float(pct) for n, (_, pct) in zip(months_since, uk, strict=True) if n >= 1],
    )

    us_share = con.execute(
        "select year, target_share_30day_fills_pct from rpt_us_prescription_share order by year"
    ).fetchall()
    uk_share = con.execute(
        """
        select year, 100.0 * sum(target_drug_items) / sum(glucose_lowering_items)
        from rpt_uk_prescription_share
        group by year
        having min(months) = 12  -- complete years, in every nation
        order by year
        """
    ).fetchall()
    return Comparison(
        us_adoption=us_adoption,
        uk_adoption=uk_adoption,
        us_share=Series([r[0] for r in us_share], [float(r[1]) for r in us_share]),
        uk_share=Series([r[0] for r in uk_share], [float(r[1]) for r in uk_share]),
        us_start=us_start,
        uk_start=f"{start:%b %Y}",
    )


def end_label(ax, series: Series, text: str, colour: str, theme: Theme, dy: int) -> None:
    """Name a line at its last point, in text ink (the line's colour is beside it)."""
    ax.annotate(
        text,
        (series.x[-1], series.y[-1]),
        xytext=(6, dy),
        textcoords="offset points",
        va="center",
        fontsize=9,
        color=theme.secondary,
    )
    ax.plot([series.x[-1]], [series.y[-1]], marker="o", markersize=5, color=colour,
            markeredgecolor=theme.surface, markeredgewidth=1.5)  # fmt: skip


def draw(data: Comparison, theme: Theme, path: Path) -> Path:
    fig = Figure(figsize=(10, 4.4), dpi=200)
    left, right = fig.subplots(1, 2)
    fig.patch.set_facecolor(theme.surface)
    us_colour, uk_colour = theme.series, UK_COLOUR[theme.name]
    line = {"linewidth": 2, "solid_joinstyle": "round", "solid_capstyle": "round"}

    # Left: adoption against years since the first adopters.
    style_axes(left, theme)
    percent_axis(left)
    left.plot(data.us_adoption.x, data.us_adoption.y, color=us_colour, label="US", **line)
    left.plot(data.uk_adoption.x, data.uk_adoption.y, color=uk_colour, label="UK", **line)
    end_label(left, data.us_adoption, f"US {data.us_adoption.y[-1]:.0f}%", us_colour, theme, 0)
    end_label(left, data.uk_adoption, f"UK {data.uk_adoption.y[-1]:.0f}%", uk_colour, theme, 0)
    left.set_xlim(0, max(data.us_adoption.x[-1], data.uk_adoption.x[-1]) + 1.2)
    left.xaxis.set_major_locator(MultipleLocator(1))
    left.xaxis.set_major_formatter(
        FuncFormatter(lambda v, _: f"{v:.0f}" if v <= data.uk_adoption.x[-1] else "")
    )
    left.set_xlabel("Years since the first adopters", fontsize=9, color=theme.muted)
    left.set_title("Share that have adopted", loc="left", fontsize=12, color=theme.text, pad=12)

    # Right: share of glucose-lowering prescriptions, by calendar year.
    style_axes(right, theme)
    right.plot(data.us_share.x, data.us_share.y, color=us_colour, label="US", **line)
    right.plot(data.uk_share.x, data.uk_share.y, color=uk_colour, label="UK", **line)
    for name, series, colour in (
        ("US", data.us_share, us_colour),
        ("UK", data.uk_share, uk_colour),
    ):
        if series.y:  # the UK has no complete year in a short sample of months
            end_label(right, series, f"{name} {series.y[-1]:.1f}%", colour, theme, 0)
    top = max(*data.us_share.y, *data.uk_share.y, 1)
    right.set_ylim(0, top * 1.15)
    right.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    years = sorted({*data.us_share.x, *data.uk_share.x})
    right.set_xticks(years)
    right.set_xlim(years[0] - 0.3, years[-1] + 1.0)
    right.set_title(
        "Share of diabetes prescriptions", loc="left", fontsize=12, color=theme.text, pad=12
    )

    legend = left.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=theme.secondary)
    legend.set_title(None)

    fig.text(
        0.01,
        0.01,
        f"Left: US Medicare Part D prescribers (first adopters {data.us_start}, yearly) and UK "
        f"GP practices (first adopters {data.uk_start}, monthly); adoption = 11+ claims in a "
        "year (US), 11+ items in 12 months (UK).\nRight: semaglutide's share of glucose-"
        "lowering prescriptions: US in 30-day fills, UK in items; complete years only. "
        "A practice is many prescribers, and Medicare is mostly people 65+.",
        fontsize=7.5,
        color=theme.muted,
        linespacing=1.5,
    )
    fig.tight_layout(rect=(0, 0.08, 1, 1), w_pad=3)
    fig.savefig(path, facecolor=theme.surface)
    return path


def render(con: duckdb.DuckDBPyConnection, out_dir: Path = IMAGES_DIR) -> list[Path]:
    data = load(con)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [draw(data, t, out_dir / f"us_uk_comparison_{t.name}.png") for t in (LIGHT, DARK)]
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
