"""Draw the epidemic curves of semaglutide adoption, for the README: US, UK, and UK by nation.

1. The adoption curve, one per country, in the same layout so the two can be compared. Two
   panels, side by side, because they measure different things on different scales (one
   chart with two y-axes would invite false comparisons):
   - left: new adopters each year (the classic epidemic curve: new cases per period);
   - right: the share of the population at risk that has ever adopted (prevalence), at
     each year's end.
   Both countries use the 11+ rule: 11+ claims in a year (US), 11+ items in 12 months (UK,
   the matched measure). The UK's monthly data are added up to years to match the US; its
   last year is partial (to the last month all four nations have published).
2. UK by nation: four small panels, one per nation, of the monthly share of practices that
   have adopted, with the UK as a whole in grey behind each for reference. One panel per
   nation, rather than four coloured lines on one chart, because the lines cross and end
   together near 100%, where labels would collide.

Each chart is drawn twice, for GitHub's light and dark themes; the README shows the one that
matches the reader's theme.

Inputs:  data/warehouse.duckdb (tables `rpt_us_adoption_curve`, `rpt_uk_adoption_curve`)
Outputs: docs/images/{us,uk}_adoption_curve_{light,dark}.png and
         docs/images/uk_nations_adoption_{light,dark}.png

Usage:
    uv run adoption-curve
"""

from __future__ import annotations

import argparse
import calendar
import logging
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from semaglutide_spread.paths import WAREHOUSE_PATH
from semaglutide_spread.viz.night_lights import IMAGES_DIR

log = logging.getLogger(__name__)

NATIONS = ("England", "Scotland", "Wales", "Northern Ireland")


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
    reference: str  # the UK-wide line behind each nation's panel


LIGHT = Theme(
    "light", "#ffffff", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#2a78d6", "#c3c2b7"
)
DARK = Theme(
    "dark", "#0d1117", "#f0efec", "#c3c2b7", "#898781", "#2c2c2a", "#383835", "#3987e5", "#52514e"
)


@dataclass
class Curve:
    years: list[int]
    new_adopters: list[int]
    prevalence_pct: list[float]
    share_title: str  # right panel's title
    footer: str
    partial_last: str | None = None  # e.g. "Jan-Jul": the last year isn't complete


@dataclass
class NationCurve:
    nation: str
    months: list[date]
    prevalence_pct: list[float]

    def first_month_at(self, pct: float) -> date | None:
        pairs = zip(self.months, self.prevalence_pct, strict=True)
        return next((month for month, share in pairs if share >= pct), None)


def load_us(con: duckdb.DuckDBPyConnection) -> Curve:
    rows = con.execute(
        "select year, new_adopters, prevalence_pct from rpt_us_adoption_curve order by year"
    ).fetchall()
    return Curve(
        [r[0] for r in rows],
        [r[1] for r in rows],
        [float(r[2]) for r in rows],
        share_title="Share of prescribers who have adopted",
        footer="Medicare Part D prescribers. Adoption: first year with 11+ semaglutide claims. "
        "Share: of each year's prescribers of any glucose-lowering drug. Source: CMS.",
    )


def last_common_month(con: duckdb.DuckDBPyConnection) -> date:
    """The last month every nation has published (the UK totals would dip after it)."""
    (month,) = con.execute(
        "select min(last) from (select max(month) as last from rpt_uk_adoption_curve "
        "group by nation)"
    ).fetchone()
    return month


def load_uk(con: duckdb.DuckDBPyConnection) -> Curve:
    """UK totals by year, on the matched (11+) measure. Prevalence is taken at each year's
    last month: December, or the last common month in the final year."""
    last = last_common_month(con)
    rows = con.execute(
        """
        with uk as (
            select month, sum(matched_new_adopters) as new_adopters,
                100.0 * sum(matched_adopted_at_risk) / sum(at_risk) as prevalence_pct
            from rpt_uk_adoption_curve
            where month <= ?
            group by month
        )
        select year(month), sum(new_adopters), arg_max(prevalence_pct, month)
        from uk group by 1 order by 1
        """,
        [last],
    ).fetchall()
    months = calendar.month_abbr
    return Curve(
        [r[0] for r in rows],
        [int(r[1]) for r in rows],
        [float(r[2]) for r in rows],
        share_title="Share of practices that have adopted",
        footer="NHS GP practices in England, Scotland, Wales and Northern Ireland. Adoption: "
        "first month with 11+ semaglutide items in 12 months.\nShare: of practices prescribing "
        "any glucose-lowering drug, at each year's end. Sources: NHSBSA, Public Health Scotland, "
        f"BSO, NWSSP. {last.year}: {months[1]} to {months[last.month]}.",
        partial_last=None if last.month == 12 else f"{months[1]}-{months[last.month]}",
    )


def load_nations(con: duckdb.DuckDBPyConnection) -> tuple[list[NationCurve], NationCurve]:
    """Each nation's monthly share of practices that have adopted, and the UK's."""
    last = last_common_month(con)
    curves = []
    for nation in NATIONS:
        rows = con.execute(
            "select month, matched_prevalence_pct from rpt_uk_adoption_curve "
            "where nation = ? and month <= ? order by month",
            [nation, last],
        ).fetchall()
        curves.append(NationCurve(nation, [r[0] for r in rows], [float(r[1]) for r in rows]))
    rows = con.execute(
        "select month, 100.0 * sum(matched_adopted_at_risk) / sum(at_risk) "
        "from rpt_uk_adoption_curve where month <= ? group by 1 order by 1",
        [last],
    ).fetchall()
    return curves, NationCurve("UK", [r[0] for r in rows], [float(r[1]) for r in rows])


def style_axes(ax, theme: Theme) -> None:
    """Recessive axes: hairline horizontal grid, no box, muted tick labels."""
    ax.set_facecolor(theme.surface)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(theme.baseline)
    ax.grid(axis="y", color=theme.grid, linewidth=1, linestyle="-")
    ax.set_axisbelow(True)
    ax.tick_params(colors=theme.muted, labelsize=10, length=0, pad=6)


def percent_axis(ax) -> None:
    ax.set_ylim(0, 100)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))


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
    left.yaxis.set_major_formatter(
        FuncFormatter(lambda v, _: f"{v / 1000:g}k" if v >= 1000 else f"{v:.0f}")
    )
    for year, n in zip(curve.years, curve.new_adopters, strict=True):
        partial = curve.partial_last and year == curve.years[-1]
        left.annotate(
            f"{n:,}\n({curve.partial_last})" if partial else f"{n:,}",
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
    percent_axis(right)
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
    right.set_title(curve.share_title, loc="left", fontsize=12, color=theme.text, pad=12)

    fig.text(0.01, 0.01, curve.footer, fontsize=8, color=theme.muted, linespacing=1.5)
    footer_lines = curve.footer.count("\n") + 1
    fig.tight_layout(rect=(0, 0.04 * footer_lines, 1, 1), w_pad=3)
    fig.savefig(path, facecolor=theme.surface)
    return path


def month_label(month: date) -> str:
    return f"{calendar.month_abbr[month.month]} {month.year}"


def draw_nations(nations: list[NationCurve], uk: NationCurve, theme: Theme, path: Path) -> Path:
    """Small multiples: one panel per nation, same axes, the UK in grey behind each."""
    fig = Figure(figsize=(10, 5.6), dpi=200)
    axes = fig.subplots(2, 2, sharex=True, sharey=True).ravel()
    fig.patch.set_facecolor(theme.surface)
    for ax, curve in zip(axes, nations, strict=True):
        style_axes(ax, theme)
        percent_axis(ax)
        ax.plot(uk.months, uk.prevalence_pct, color=theme.reference, linewidth=1.5)
        ax.plot(
            curve.months,
            curve.prevalence_pct,
            color=theme.series,
            linewidth=2,
            solid_joinstyle="round",
            solid_capstyle="round",
        )
        half = curve.first_month_at(50)
        subtitle = f"half of practices by {month_label(half)}" if half else ""
        if half:
            ax.plot(
                [half],
                [curve.prevalence_pct[curve.months.index(half)]],
                marker="o",
                markersize=6,
                color=theme.series,
                markeredgecolor=theme.surface,
                markeredgewidth=1.5,
            )
        ax.set_title(curve.nation, loc="left", fontsize=12, color=theme.text, pad=18)
        ax.text(0, 1.03, subtitle, transform=ax.transAxes, fontsize=9, color=theme.secondary)
        ax.set_xlim(uk.months[0], uk.months[-1])
        ax.set_xticks([date(y, 1, 1) for y in range(uk.months[0].year, uk.months[-1].year + 1)])
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: ""))
    for ax in axes[2:]:
        ax.set_xticklabels(
            [f"{y}" for y in range(uk.months[0].year, uk.months[-1].year + 1)], fontsize=9
        )
    last = uk.months[-1]
    fig.text(
        0.01,
        0.01,
        "Monthly share of GP practices prescribing any glucose-lowering drug that have adopted "
        "semaglutide (11+ items in 12 months). Grey: the UK as a whole. "
        f"{month_label(uk.months[0])} to {month_label(last)}.",
        fontsize=8,
        color=theme.muted,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1), h_pad=2.5, w_pad=3)
    fig.savefig(path, facecolor=theme.surface)
    return path


def render(con: duckdb.DuckDBPyConnection, out_dir: Path = IMAGES_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for country, curve in (("us", load_us(con)), ("uk", load_uk(con))):
        for theme in (LIGHT, DARK):
            paths.append(draw(curve, theme, out_dir / f"{country}_adoption_curve_{theme.name}.png"))
    nations, uk = load_nations(con)
    for theme in (LIGHT, DARK):
        paths.append(
            draw_nations(nations, uk, theme, out_dir / f"uk_nations_adoption_{theme.name}.png")
        )
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
