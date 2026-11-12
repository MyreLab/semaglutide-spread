"""Draw "Who prescribes it": the US adopters from specialties that rarely manage diabetes.

Most adopters are where you'd expect (primary care, nurse practitioners, endocrinology), so
a chart of every specialty would be one long bar and a lot of slivers. Instead, the subtitle
gives the split between specialties expected to prescribe diabetes drugs and specialties
that rarely manage diabetes (seed `specialty_expectations`, decision D47), and the chart
lists the second: one
horizontal bar per specialty, longest first, the smallest folded into one "Other" bar.

Inputs:  data/warehouse.duckdb (table `rpt_us_adopters_by_specialty`)
Outputs: docs/images/us_unexpected_specialties_{light,dark}.png

Usage:
    uv run specialty-chart
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb
from matplotlib.figure import Figure

from semaglutide_spread.paths import WAREHOUSE_PATH
from semaglutide_spread.viz.adoption_curve import DARK, LIGHT, Theme
from semaglutide_spread.viz.night_lights import IMAGES_DIR

log = logging.getLogger(__name__)

SHOWN = 20  # bars before the rest are folded into "Other"


@dataclass
class Specialties:
    names: list[str]  # longest bar first
    adopters: list[int]
    share_pct: dict[str, float]  # expectation -> % of all adopters
    unexpected: int
    unknown: int  # adopters with no specialty (mostly organisations)
    total: int


def load(con: duckdb.DuckDBPyConnection, shown: int = SHOWN) -> Specialties:
    rows = con.execute(
        "select specialty, adopters from rpt_us_adopters_by_specialty "
        "where expectation = 'unexpected' order by adopters desc, specialty"
    ).fetchall()
    names, counts = [r[0] for r in rows[:shown]], [r[1] for r in rows[:shown]]
    rest = rows[shown:]
    if rest:
        names.append(f"Other ({len(rest)} {'specialty' if len(rest) == 1 else 'specialties'})")
        counts.append(sum(r[1] for r in rest))
    shares = dict(
        con.execute(
            "select expectation, 100.0 * sum(adopters) / sum(sum(adopters)) over () "
            "from rpt_us_adopters_by_specialty group by expectation"
        ).fetchall()
    )
    (total,) = con.execute("select sum(adopters) from rpt_us_adopters_by_specialty").fetchone()
    (unknown,) = con.execute(
        "select coalesce(sum(adopters), 0) from rpt_us_adopters_by_specialty "
        "where expectation = 'unknown'"
    ).fetchone()
    return Specialties(
        names, counts, shares, sum(r[1] for r in rows), int(unknown), int(total or 0)
    )


def draw(data: Specialties, theme: Theme, path: Path) -> Path:
    height = 1.6 + 0.27 * len(data.names)
    fig = Figure(figsize=(8, height), dpi=200)
    ax = fig.subplots()
    fig.patch.set_facecolor(theme.surface)
    ax.set_facecolor(theme.surface)

    positions = list(range(len(data.names)))[::-1]  # longest at the top
    # The folded "Other" bar is grey: it's many specialties, not one.
    colours = [theme.series] * len(data.names)
    if data.names and data.names[-1].startswith("Other ("):
        colours[-1] = theme.reference
    ax.barh(positions, data.adopters, height=0.62, color=colours)
    ax.set_ylim(-0.6, len(data.names) - 0.4)
    ax.set_yticks(positions, data.names, fontsize=9, color=theme.secondary)
    for y, n in zip(positions, data.adopters, strict=True):
        ax.annotate(f"{n:,}", (n, y), xytext=(4, 0), textcoords="offset points", va="center",
                    fontsize=8.5, color=theme.secondary)  # fmt: skip
    ax.set_xlim(0, max(data.adopters, default=1) * 1.12)
    ax.set_xticks([])
    for side in ("top", "right", "bottom"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(theme.baseline)
    ax.tick_params(axis="y", length=0, pad=6)

    pct = data.share_pct
    fig.suptitle(
        f"{data.unexpected:,} adopters came from specialties that rarely manage diabetes",
        x=0.01, ha="left", fontsize=12, color=theme.text,
    )  # fmt: skip
    fig.text(
        0.01,
        1 - 0.45 / height,
        f"Of {data.total:,} US adopters, {pct.get('expected', 0):.1f}% came from specialties "
        "expected to prescribe diabetes drugs (such as primary care,\nendocrinology, nurse "
        f"practitioners and cardiology) and {pct.get('unexpected', 0):.1f}% from specialties "
        "that rarely manage diabetes",
        fontsize=8.5,
        color=theme.secondary,
        va="top",
        linespacing=1.5,
    )
    fig.text(
        0.01,
        0.01,
        "Medicare Part D prescribers with 11+ semaglutide claims in a year, 2018-2024, by "
        "specialty in their adoption year.\nThe data can't say why semaglutide was "
        f"prescribed. Specialty names as CMS writes them. {data.unknown:,} adopters with no "
        "specialty aren't shown.",
        fontsize=7.5,
        color=theme.muted,
        linespacing=1.5,
    )
    fig.tight_layout(rect=(0, 0.45 / height, 1, 1 - 0.8 / height))
    fig.savefig(path, facecolor=theme.surface)
    return path


def render(con: duckdb.DuckDBPyConnection, out_dir: Path = IMAGES_DIR) -> list[Path]:
    data = load(con)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        draw(data, theme, out_dir / f"us_unexpected_specialties_{theme.name}.png")
        for theme in (LIGHT, DARK)
    ]
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
