"""Animate semaglutide's spread across the US as a "night-lights" map.

Each prescriber in the population at risk is a faint blue speck, so the map of US diabetes
prescribing appears on its own, without drawn borders. When a prescriber adopts semaglutide
their light comes on: a bright flash that settles into a steady warm glow. Places with many
adopters glow brighter, like cities seen from space at night.

Steps:
1. Read adopters (adoption year, location) and each year's population at risk from the
   warehouse; keep the contiguous US (Alaska, Hawaii and territories need insets: not yet).
2. Project latitude/longitude to pixels with an Albers equal-area projection, the standard
   projection for US maps (areas are not distorted, so density reads correctly).
3. Give each adopter a moment within their adoption year. The data are yearly, so this
   moment is random (fixed by the prescriber's NPI, so every run gives the same GIF); it lets
   the yearly data animate smoothly instead of jumping once a year.
4. For each frame: add up every prescriber's light at its pixel, blur it twice (a tight core
   and a wide halo) for the glow, convert brightness to colour, and write the year and a
   running count of adopters.
5. Save the frames as a looping GIF, holding the last frame, plus a full-size PNG of the end.

Inputs:  data/warehouse.duckdb (tables `adopters`, `population_at_risk`, `rpt_adoption_curve`)
Outputs: docs/images/us_night_lights.gif and docs/images/us_night_lights_<last year>.png

Usage:
    uv run night-lights
    uv run night-lights --width 1600 --frames-per-year 12   # bigger, smoother, larger file
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from semaglutide_spread.paths import PROJECT_ROOT, WAREHOUSE_PATH

log = logging.getLogger(__name__)

IMAGES_DIR = PROJECT_ROOT / "docs" / "images"

# Contiguous US bounding box (degrees). Alaska, Hawaii and the territories fall outside it.
LAT_RANGE = (24.0, 50.0)
LON_RANGE = (-125.0, -66.0)

# USA Contiguous Albers Equal Area Conic (EPSG:5070): standard parallels and origin.
PARALLELS = (29.5, 45.5)
ORIGIN = (23.0, -96.0)

BACKGROUND = np.array([4, 6, 14], dtype=np.float32)  # near-black navy
COOL = np.array([32, 50, 92], dtype=np.float32)  # population at risk: faint blue
WARM = np.array([255, 176, 70], dtype=np.float32)  # adopters: amber
HOT = np.array([255, 248, 225], dtype=np.float32)  # dense adopters / flashes: near-white
TEXT = (235, 230, 215)
WARM_TEXT = tuple(int(v) for v in WARM)
SUBTLE = (130, 135, 150)

FLASH_LENGTH = 0.35  # years for a new light to settle from flash to steady glow
FLASH_PEAK = 4.0  # brightness of a flash, relative to a steady light


@dataclass
class MapData:
    adopter_x: np.ndarray  # pixel column of each adopter
    adopter_y: np.ndarray  # pixel row
    adopter_time: np.ndarray  # adoption moment in years, e.g. 2019.37
    at_risk: dict[int, tuple[np.ndarray, np.ndarray]]  # year -> (x, y) of at-risk prescribers
    first_year: int
    last_year: int
    prevalence_pct: dict[int, float]  # year -> % of population at risk that has adopted


def albers(lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Albers equal-area conic projection on a unit sphere: (x, y), y pointing north."""
    phi1, phi2 = np.radians(PARALLELS)
    phi0, lam0 = np.radians(ORIGIN)
    phi, lam = np.radians(lat), np.radians(lon)
    n = (np.sin(phi1) + np.sin(phi2)) / 2
    c = np.cos(phi1) ** 2 + 2 * n * np.sin(phi1)
    rho0 = np.sqrt(c - 2 * n * np.sin(phi0)) / n
    rho = np.sqrt(c - 2 * n * np.sin(phi)) / n
    theta = n * (lam - lam0)
    return rho * np.sin(theta), rho0 - rho * np.cos(theta)


class PixelGrid:
    """Turns latitude/longitude into pixel positions for an image of a given width."""

    def __init__(self, width: int, margin: int, top: int):
        # Project the corners and edges of the bounding box to find the map's extent.
        lats = np.linspace(*LAT_RANGE, 50)
        lons = np.linspace(*LON_RANGE, 50)
        edge_lat = np.concatenate([lats, lats, np.full(50, LAT_RANGE[0]), np.full(50, 49.0)])
        edge_lon = np.concatenate(
            [np.full(50, LON_RANGE[0]), np.full(50, LON_RANGE[1]), lons, lons]
        )
        x, y = albers(edge_lat, edge_lon)
        self.x_min, self.y_max = x.min(), y.max()
        self.scale = (width - 2 * margin) / (x.max() - x.min())
        self.margin, self.top = margin, top
        self.width = width
        self.height = round((y.max() - y.min()) * self.scale) + top + margin

    def to_pixels(self, lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x, y = albers(lat, lon)
        col = (x - self.x_min) * self.scale + self.margin
        row = (self.y_max - y) * self.scale + self.top
        return col, row


def stable_fraction(keys: list[str], salt: str) -> np.ndarray:
    """A number in [0, 1) for each key, the same on every run (unlike a random generator)."""
    return np.array(
        [
            int.from_bytes(hashlib.md5(f"{salt}:{k}".encode()).digest()[:8], "big") / 2**64
            for k in keys
        ]
    )


def jitter(keys: list[str], spread: float) -> tuple[np.ndarray, np.ndarray]:
    """Small fixed offsets (pixels), so prescribers sharing a ZIP-area point form a cluster
    of lights rather than a single pixel."""
    u1 = np.clip(stable_fraction(keys, "jx"), 1e-9, None)
    u2 = stable_fraction(keys, "jy")
    radius = spread * np.sqrt(-2 * np.log(u1))  # Box-Muller: normally distributed offsets
    return radius * np.cos(2 * np.pi * u2), radius * np.sin(2 * np.pi * u2)


def load(con: duckdb.DuckDBPyConnection, grid: PixelGrid) -> MapData:
    box = (
        f"latitude between {LAT_RANGE[0]} and {LAT_RANGE[1]} "
        f"and longitude between {LON_RANGE[0]} and {LON_RANGE[1]}"
    )
    adopters = con.execute(
        f"select npi, adoption_year, latitude, longitude from adopters where {box} order by npi"
    ).fetchall()
    npis = [r[0] for r in adopters]
    years = np.array([r[1] for r in adopters], dtype=np.float64)
    col, row = grid.to_pixels(
        np.array([r[2] for r in adopters]), np.array([r[3] for r in adopters])
    )
    dx, dy = jitter(npis, spread=0.8)

    at_risk = {}
    for (year,) in con.execute(
        "select distinct year from population_at_risk order by 1"
    ).fetchall():
        rows = con.execute(
            f"select npi, latitude, longitude from population_at_risk where year = ? and {box}",
            [year],
        ).fetchall()
        c, r = grid.to_pixels(np.array([x[1] for x in rows]), np.array([x[2] for x in rows]))
        jx, jy = jitter([x[0] for x in rows], spread=0.8)
        at_risk[year] = (c + jx, r + jy)

    prevalence = dict(con.execute("select year, prevalence_pct from rpt_adoption_curve").fetchall())
    return MapData(
        adopter_x=col + dx,
        adopter_y=row + dy,
        adopter_time=years + stable_fraction(npis, "t"),
        at_risk=at_risk,
        first_year=min(at_risk),
        last_year=max(at_risk),
        prevalence_pct={int(k): float(v) for k, v in prevalence.items()},
    )


def accumulate(x: np.ndarray, y: np.ndarray, weights: np.ndarray, shape) -> np.ndarray:
    """Add each point's weight to its pixel. Returns a float image."""
    h, w = shape
    xi, yi = np.rint(x).astype(int), np.rint(y).astype(int)
    keep = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
    flat = np.bincount(yi[keep] * w + xi[keep], weights=weights[keep], minlength=h * w)
    return flat.reshape(h, w)


def box_blur(image: np.ndarray, radius: int) -> np.ndarray:
    """Average over a (2r+1)-pixel square, using running sums (fast for any radius)."""
    if radius < 1:
        return image
    out = image
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (radius + 1, radius)
        summed = np.cumsum(np.pad(out, pad), axis=axis)
        n = out.shape[axis]
        out = (
            np.take(summed, range(2 * radius + 1, 2 * radius + 1 + n), axis=axis)
            - np.take(summed, range(n), axis=axis)
        ) / (2 * radius + 1)
    return out


def glow(image: np.ndarray, radius: int) -> np.ndarray:
    """Three box blurs in a row approximate a Gaussian blur."""
    for _ in range(3):
        image = box_blur(image, radius)
    return image


def light_curve(age: np.ndarray) -> np.ndarray:
    """Brightness of a light `age` years after it comes on: a flash fading to a steady 1."""
    flash = (FLASH_PEAK - 1) * np.clip(1 - age / FLASH_LENGTH, 0, 1) ** 2
    return np.where(age >= 0, 1 + flash, 0.0)


def tone(light: np.ndarray, strength: float) -> np.ndarray:
    """Map accumulated light (0 to large) to 0-1 brightness, compressing bright cities."""
    return 1 - np.exp(-strength * light)


def year_of(t: float, data: MapData) -> int:
    """The calendar year a frame belongs to. Frame times are year ends (2019.0 closes 2018)."""
    return min(int(t - 1e-9), data.last_year)


def render_frame(data: MapData, grid: PixelGrid, t: float) -> np.ndarray:
    """One RGB frame (uint8) at time t (in years)."""
    shape = (grid.height, grid.width)
    year = year_of(t, data)

    rx, ry = data.at_risk[year]
    at_risk = accumulate(rx, ry, np.ones_like(rx), shape)
    at_risk = tone(glow(at_risk, 1) * 3, 0.2)

    weights = light_curve(t - data.adopter_time)
    lit = weights > 0
    adopters = accumulate(data.adopter_x[lit], data.adopter_y[lit], weights[lit], shape)
    core = tone(glow(adopters, 1) * 3, 0.16)
    halo = tone(glow(adopters, 4) * 3, 0.06)

    warm = np.clip(0.8 * core + 0.35 * halo, 0, 1)[..., None]
    hot = np.clip(core - 0.75, 0, 1)[..., None] / 0.25
    rgb = BACKGROUND + at_risk[..., None] * COOL * (1 - warm)
    rgb = rgb + warm * (WARM - rgb)
    rgb = rgb + hot * (HOT - rgb)
    return np.clip(rgb, 0, 255).astype(np.uint8)


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def annotate(frame: np.ndarray, data: MapData, t: float, adopted: int) -> Image.Image:
    """Add the title, the date, the running count and the source line."""
    image = Image.fromarray(frame)
    draw = ImageDraw.Draw(image)
    w, h = image.size
    s = w / 960  # text scales with image width
    pad = int(24 * s)
    draw.text(
        (pad, pad),
        "Semaglutide (Ozempic) spreading through US prescribers",
        font=font(int(22 * s)),
        fill=TEXT,
    )
    draw.text(
        (pad, pad + int(30 * s)),
        "Each light is a Medicare Part D prescriber; "
        "it comes on in the year they first prescribe semaglutide",
        font=font(int(13 * s)),
        fill=SUBTLE,
    )

    year = year_of(t, data)
    draw.text((pad, h - int(150 * s)), str(year), font=font(int(64 * s)), fill=TEXT)
    draw.text(
        (pad, h - int(78 * s)), f"{adopted:,} prescribers", font=font(int(20 * s)), fill=WARM_TEXT
    )
    pct = data.prevalence_pct.get(year)
    if pct is not None:
        shown = f"{pct:.1f}" if pct < 10 else f"{pct:.0f}"
        draw.text(
            (pad, h - int(52 * s)),
            f"{shown}% of {year}'s diabetes-drug prescribers had adopted by year end",
            font=font(int(13 * s)),
            fill=SUBTLE,
        )
    source = (
        f"Data: CMS Medicare Part D Prescribers, {data.first_year}-{data.last_year}. "
        "Contiguous US. Yearly data: timing within each year is illustrative."
    )
    draw.text((pad, h - int(24 * s)), source, font=font(int(11 * s)), fill=SUBTLE)
    return image


def frame_times(first: int, last: int, per_year: int) -> list[float]:
    """Frame times from the start of the first year to the end of the last."""
    steps = (last - first + 1) * per_year
    return [first + (i + 1) / per_year for i in range(steps)]


def to_palette(frames: list[Image.Image]) -> list[Image.Image]:
    """Convert every frame to one shared 256-colour palette (GIFs allow 256 colours), so
    colours don't flicker between frames."""
    # Build the palette from a strip of frames across the animation, so the early blues are
    # in it as well as the late ambers.
    picks = [*frames[:: max(1, len(frames) // 6)], frames[-1]]
    w, h = frames[0].size
    strip = Image.new("RGB", (w, h * len(picks)))
    for i, frame in enumerate(picks):
        strip.paste(frame, (0, i * h))
    reference = strip.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    return [f.quantize(palette=reference, dither=Image.Dither.NONE) for f in frames]


def render(
    con: duckdb.DuckDBPyConnection,
    out_dir: Path = IMAGES_DIR,
    width: int = 960,
    frames_per_year: int = 8,
    frame_ms: int = 90,
    hold_ms: int = 4000,
) -> tuple[Path, Path]:
    """Draw every frame from the warehouse behind `con`; save the GIF and the final PNG."""
    grid = PixelGrid(width, margin=int(20 * width / 960), top=int(70 * width / 960))
    data = load(con, grid)
    log.info("%s adopters on the map", f"{len(data.adopter_time):,}")

    frames = []
    for t in frame_times(data.first_year, data.last_year, frames_per_year):
        adopted = int((data.adopter_time <= t).sum())
        frames.append(annotate(render_frame(data, grid, t), data, t, adopted))

    out_dir.mkdir(parents=True, exist_ok=True)
    png_path = out_dir / f"us_night_lights_{data.last_year}.png"
    gif_path = out_dir / "us_night_lights.gif"
    frames[-1].save(png_path, optimize=True)

    durations = [frame_ms] * len(frames)
    durations[-1] = hold_ms  # hold the final frame before the loop restarts
    first, *rest = to_palette(frames)
    first.save(
        gif_path, save_all=True, append_images=rest, duration=durations, loop=0, optimize=True
    )
    log.info(
        "Wrote %s (%.1f MB) and %s", gif_path.name, gif_path.stat().st_size / 1e6, png_path.name
    )
    return gif_path, png_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--width", type=int, default=960, help="image width in pixels")
    parser.add_argument("--frames-per-year", type=int, default=8)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with duckdb.connect(str(WAREHOUSE_PATH), read_only=True) as con:
        render(con, width=args.width, frames_per_year=args.frames_per_year)
    return 0


if __name__ == "__main__":
    sys.exit(main())
