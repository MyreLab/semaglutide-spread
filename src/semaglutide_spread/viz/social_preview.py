"""Draw the repository's social preview: both night-lights maps side by side, at the end of
their data, with a title. GitHub shows it wherever the repository's link is shared, and it
doubles as the portfolio thumbnail.

1. Draw each map's final frame (no text) near the size it will appear: the US about 880
   pixels wide, the UK about 280. Each light is brightened as its map shrinks, since more
   lights then share each pixel; the UK is brightened less, as its practices are close
   together and would otherwise merge into blobs.
2. Crop each map to its lights (the projection box has empty sea and land around the
   country), then scale both to the same height.
3. Lay them out on GitHub's recommended 1280 x 640 canvas, under the title, with each map's
   count of adopters beneath it, aligned to the map's outer edge.

Inputs:  data/warehouse.duckdb (the night-lights tables)
Output:  docs/images/social_preview.png (set it in GitHub: Settings > General > Social preview)

Usage:
    uv run social-preview
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

log = logging.getLogger(__name__)

SIZE = (1280, 640)  # GitHub's recommended social preview size
MAP_HEIGHT = 420
TITLE = "Tracking semaglutide's spread: US & UK"
SUBTITLE = "Ozempic, Rybelsus and Wegovy across US prescribers and UK GP practices"


def final_frame(
    con: duckdb.DuckDBPyConnection, country: nl.Country, map_width: int, gain: float
) -> tuple[Image.Image, nl.MapData]:
    """The country's map at the end of its data, `map_width` pixels wide, without text."""
    country = dataclasses.replace(country, light_gain=gain)
    grid = nl.PixelGrid(map_width + 20, margin=10, top=10, country=country, map_width=map_width)
    data = nl.LOADERS[country.code](con, grid, country.jitter_px * map_width / country.map_width)
    end = (data.last_period + 1) / country.periods_per_year
    return Image.fromarray(nl.render_frame(data, grid, end)), data


def crop_to_lights(image: Image.Image, pad: int = 8) -> Image.Image:
    """Crop to the lit pixels (anything visibly brighter than the background)."""
    pixels = np.asarray(image).astype(int)
    lit = np.abs(pixels - nl.BACKGROUND).sum(axis=2) > 30
    if not lit.any():
        return image
    rows, cols = np.where(lit)
    return image.crop((cols.min() - pad, rows.min() - pad, cols.max() + pad, rows.max() + pad))


def fit_height(image: Image.Image, height: int) -> Image.Image:
    width = round(image.size[0] * height / image.size[1])
    return image.resize((width, height), Image.Resampling.LANCZOS)


def label(country: nl.Country, data: nl.MapData) -> str:
    """E.g. "US: 199,719 prescribers, 2018-2024"."""
    first = data.first_period // country.periods_per_year
    last = data.last_period // country.periods_per_year
    adopters = len(data.adopter_time)
    return f"{country.code.upper()}: {adopters:,} {country.unit}, {first}-{last}"


def render(con: duckdb.DuckDBPyConnection, out_dir: Path = nl.IMAGES_DIR) -> Path:
    us, us_data = final_frame(con, nl.US, 880, nl.US.light_gain * (nl.US.map_width / 880) ** 2)
    uk, uk_data = final_frame(
        con, nl.UK, 280, nl.UK.light_gain * (nl.UK.map_width / 280) ** 2 * 0.4
    )
    us, uk = (fit_height(crop_to_lights(m), MAP_HEIGHT) for m in (us, uk))

    width, height = SIZE
    label_font = nl.font(19)
    labels = [label(country, data) for country, data in ((nl.US, us_data), (nl.UK, uk_data))]

    # Centre the two maps, 70 pixels apart, on the canvas. Each label sits under the outer edge
    # of its map (the US label from the US map's left edge, the UK label to the UK map's right
    # edge), so the left and right margins match for the labels as well as the maps.
    between = 70
    us_left = (width - us.size[0] - uk.size[0] - between) // 2
    uk_left = us_left + us.size[0] + between

    canvas = Image.new("RGB", SIZE, tuple(int(v) for v in nl.BACKGROUND))
    draw = ImageDraw.Draw(canvas)
    draw.text((width // 2, 52), TITLE, font=nl.font(46), fill=nl.TEXT, anchor="mm")
    draw.text((width // 2, 98), SUBTITLE, font=nl.font(20), fill=nl.SUBTLE, anchor="mm")
    canvas.paste(us, (us_left, 135))
    canvas.paste(uk, (uk_left, 135))
    y = height - 36
    draw.text((us_left, y), labels[0], font=label_font, fill=nl.WARM_TEXT, anchor="lm")
    draw.text((uk_left + uk.size[0], y), labels[1], font=label_font, fill=nl.WARM_TEXT, anchor="rm")

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "social_preview.png"
    canvas.save(path, optimize=True)
    log.info("Wrote %s (%.0f KB)", path.name, path.stat().st_size / 1e3)
    return path


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.split("\n")[0]).parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with duckdb.connect(str(WAREHOUSE_PATH), read_only=True) as con:
        render(con)
    return 0


if __name__ == "__main__":
    sys.exit(main())
