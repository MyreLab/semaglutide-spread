"""Animate both night-lights maps in one GIF, side by side on a shared clock.

The single-country GIFs (`uv run night-lights`) loop at different lengths, so shown next to
each other they drift apart. Here the US and UK maps are frames of one animation: each frame
is one moment in time for both countries, under one title and one year label. The US map
stops changing after 2024, its last year of data, and stays lit while the UK map carries on
to its last month.

Steps:
1. Load each country's adopters as the single-country maps do (`night_lights.LOADERS`), drawn
   near the size they will appear: each light brightened as its map shrinks, since more
   lights then share a pixel (as in the social preview; the UK a little dimmer, so its
   lights don't merge into blobs while many practices flash at once in 2019-2020).
2. Crop each map to the lights of its final frame, and use that same crop for every frame, so
   the maps don't move.
3. For each moment (10 frames a year, the same pace as the single maps): draw both maps,
   scale them to the same height, and lay them out under the title, with the year above and
   each country's running count of adopters below its map.
4. Save as a looping GIF with one shared colour palette, holding the last frame.

Inputs:  data/warehouse.duckdb (the night-lights tables)
Output:  docs/images/us_uk_night_lights.gif

Usage:
    uv run night-lights-combined
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from pathlib import Path

import duckdb
import numpy as np
from PIL import Image, ImageDraw

from semaglutide_spread.paths import WAREHOUSE_PATH
from semaglutide_spread.viz import night_lights as nl
from semaglutide_spread.viz.social_preview import SUBTITLE, TITLE, fit_height

log = logging.getLogger(__name__)

WIDTH = 960
MAP_HEIGHT = 410
BETWEEN = 60  # pixels between the two maps
TOP = 150  # where the maps start, below the title and year


@dataclasses.dataclass
class CountryMap:
    country: nl.Country
    grid: nl.PixelGrid
    data: nl.MapData
    box: tuple[int, int, int, int]  # crop to the lights of the final frame

    @property
    def end(self) -> float:
        """The end of the country's last period, in years (2025.0 for the US's 2024)."""
        return (self.data.last_period + 1) / self.country.periods_per_year

    def frame(self, t: float, height: int) -> Image.Image:
        image = Image.fromarray(nl.render_frame(self.data, self.grid, t)).crop(self.box)
        return fit_height(image, height)

    def adopted(self, t: float) -> int:
        return int((self.data.adopter_time <= t).sum())


def lights_box(frame: np.ndarray, pad: int = 8) -> tuple[int, int, int, int]:
    """The box around every pixel visibly brighter than the background."""
    lit = np.abs(frame.astype(int) - nl.BACKGROUND).sum(axis=2) > 30
    if not lit.any():
        return (0, 0, frame.shape[1], frame.shape[0])
    rows, cols = np.where(lit)
    return (int(cols.min()) - pad, int(rows.min()) - pad,
            int(cols.max()) + pad + 1, int(rows.max()) + pad + 1)  # fmt: skip


def load(
    con: duckdb.DuckDBPyConnection, country: nl.Country, map_width: int, gain: float
) -> CountryMap:
    country = dataclasses.replace(country, light_gain=gain)
    grid = nl.PixelGrid(map_width + 20, margin=10, top=10, country=country, map_width=map_width)
    data = nl.LOADERS[country.code](con, grid, country.jitter_px * map_width / country.map_width)
    end = (data.last_period + 1) / country.periods_per_year
    return CountryMap(country, grid, data, lights_box(nl.render_frame(data, grid, end)))


def count_label(m: CountryMap, t: float) -> str:
    """E.g. "US: 63,477 prescribers"; after the country's data end, says when they end."""
    text = f"{m.country.code.upper()}: {m.adopted(t):,} {m.country.unit}"
    if t > m.end + 1e-9:
        last = m.data.last_period // m.country.periods_per_year
        text += f" (data to {last})"
    return text


def draw_frame(us: CountryMap, uk: CountryMap, t: float, width: int = WIDTH) -> Image.Image:
    s = width / WIDTH
    map_height = round(MAP_HEIGHT * s)
    us_image, uk_image = us.frame(t, map_height), uk.frame(t, map_height)
    us_left = (width - us_image.size[0] - uk_image.size[0] - round(BETWEEN * s)) // 2
    uk_left = us_left + us_image.size[0] + round(BETWEEN * s)
    top = round(TOP * s)

    height = top + map_height + round(56 * s)
    canvas = Image.new("RGB", (width, height), tuple(int(v) for v in nl.BACKGROUND))
    canvas.paste(us_image, (us_left, top))
    canvas.paste(uk_image, (uk_left, top))

    draw = ImageDraw.Draw(canvas)
    centre = width // 2
    draw.text((centre, round(32 * s)), TITLE, font=nl.font(round(30 * s)), fill=nl.TEXT,
              anchor="mm")  # fmt: skip
    draw.text((centre, round(64 * s)), SUBTITLE, font=nl.font(round(16 * s)), fill=nl.SUBTLE,
              anchor="mm")  # fmt: skip
    year = int(t - 1e-9)  # a frame shows the moment it ends: 2019.0 closes 2018
    draw.text((centre, round(112 * s)), str(year), font=nl.font(round(44 * s)), fill=nl.TEXT,
              anchor="mm")  # fmt: skip

    y = top + map_height + round(28 * s)
    label_font = nl.font(round(18 * s))
    draw.text((us_left, y), count_label(us, t), font=label_font, fill=nl.WARM_TEXT, anchor="lm")
    draw.text((uk_left + uk_image.size[0], y), count_label(uk, t), font=label_font,
              fill=nl.WARM_TEXT, anchor="rm")  # fmt: skip
    return canvas


def render(
    con: duckdb.DuckDBPyConnection,
    out_dir: Path = nl.IMAGES_DIR,
    width: int = WIDTH,
    frames_per_year: int = 10,
    frame_ms: int = 120,
    hold_ms: int = 4000,
) -> Path:
    """Draw every frame and save the GIF. Same pace as the single-country maps by default."""
    us = load(con, nl.US, 880, nl.US.light_gain * (nl.US.map_width / 880) ** 2)
    uk = load(con, nl.UK, 280, nl.UK.light_gain * (nl.UK.map_width / 280) ** 2 * 0.3)
    start = min(us.data.first_period / us.country.periods_per_year,
                uk.data.first_period / uk.country.periods_per_year)  # fmt: skip
    times = nl.frame_times(start, max(us.end, uk.end), frames_per_year)
    frames = [draw_frame(us, uk, t, width) for t in times]

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "us_uk_night_lights.gif"
    durations = [frame_ms] * len(frames)
    durations[-1] = hold_ms
    first, *rest = nl.to_palette(frames)
    first.save(path, save_all=True, append_images=rest, duration=durations, loop=0, optimize=True)
    log.info("Wrote %s: %d frames, %.1f MB", path.name, len(frames), path.stat().st_size / 1e6)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--width", type=int, default=WIDTH, help="image width in pixels")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with duckdb.connect(str(WAREHOUSE_PATH), read_only=True) as con:
        render(con, width=args.width)
    return 0


if __name__ == "__main__":
    sys.exit(main())
