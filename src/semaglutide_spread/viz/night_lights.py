"""Animate semaglutide's spread as a "night-lights" map, for the US and for the UK.

The map starts dark. When a prescriber (US) or GP practice (UK) adopts semaglutide, its light
comes on: a bright flash that settles into a steady warm glow. Places with many adopters glow
brighter, like cities seen from space at night. With --show-population-at-risk, every
prescriber or practice of any diabetes drug is also drawn as a faint blue speck (the
denominator), so the country's outline is there from the start.

The two maps share all the drawing code; a `Country` holds what differs: the area shown, the
projection, the time step of the data (years in the US, months in the UK), the text, and how
bright one light is.

Steps:
1. Read adopters (adoption date, location) and each period's population at risk from the
   warehouse. The US map shows the contiguous US (Alaska, Hawaii and territories need insets:
   not yet); the UK map shows all four nations, using each practice's first month whose
   past 12 months add up to 11+ items (the measure matched to the US rule of 11+ claims in a
   year), up to the last month every nation has published.
2. Project latitude/longitude to pixels with an Albers equal-area projection (areas are not
   distorted, so density reads correctly), with standard parallels suited to each country.
3. Give each adopter a moment within their adoption period (a year in the US, a month in the
   UK). The moment is random but fixed by the adopter's ID, so every run gives the same GIF;
   it lets the data animate smoothly instead of jumping once a period.
4. For each frame: add up every adopter's light at its pixel, blur it twice (a tight core and
   a wide halo) for the glow, convert brightness to colour, and write the date and a running
   count of adopters.
5. Save the frames as a looping GIF, holding the last frame, plus a full-size PNG of the end.

Inputs:  data/warehouse.duckdb (`us_adopters`, `us_population_at_risk`, `rpt_us_adoption_curve`,
         `uk_adopters`, `uk_population_at_risk`, `rpt_uk_adoption_curve`)
Outputs: docs/images/{us,uk}_night_lights.gif and docs/images/{us,uk}_night_lights_<end>.png

Usage:
    uv run night-lights                       # both maps
    uv run night-lights --country uk
    uv run night-lights --show-population-at-risk   # add the blue denominator layer
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from semaglutide_spread.paths import PROJECT_ROOT, WAREHOUSE_PATH

log = logging.getLogger(__name__)

IMAGES_DIR = PROJECT_ROOT / "docs" / "images"

BACKGROUND = np.array([6, 13, 32], dtype=np.float32)  # dark navy
COOL = np.array([32, 50, 92], dtype=np.float32)  # population at risk: faint blue
WARM = np.array([255, 176, 70], dtype=np.float32)  # adopters: amber
HOT = np.array([255, 248, 225], dtype=np.float32)  # dense adopters / flashes: near-white
TEXT = (235, 230, 215)
WARM_TEXT = tuple(int(v) for v in WARM)
SUBTLE = (130, 135, 150)

# Image size and colours. The GIFs are drawn 720 pixels wide with 64 colours: half the file
# size of 960 pixels and 255 colours (the US GIF: 2.3 MB instead of 4.6 MB), so the README
# loads faster, with no visible loss (the map is mostly dark navy and shades of amber).
WIDTH = 720
COLORS = 64

FLASH_LENGTH = 0.35  # years for a new light to settle from flash to steady glow
FLASH_PEAK = 4.0  # brightness of a flash, relative to a steady light


@dataclass(frozen=True)
class Country:
    """Everything that differs between the US and UK maps."""

    code: str  # used in file names: "us", "uk"
    lat_range: tuple[float, float]  # area shown (degrees)
    lon_range: tuple[float, float]
    top_lat: float  # where the map is cropped at the top (Albers bends the box's top edge)
    parallels: tuple[float, float]  # Albers standard parallels
    origin: tuple[float, float]  # Albers origin (latitude, longitude)
    periods_per_year: int  # 1: yearly data (US); 12: monthly data (UK)
    title: str
    subtitle: str
    unit: str  # what one light is, in the running count: "prescribers", "GP practices"
    prevalence_text: str  # after "<pct>% of ", e.g. "this year's diabetes-drug prescribers"
    source: str  # with {start} and {end} for the first and last period's labels
    light_gain: float  # how bright one light is (a UK practice is one light for many doctors)
    jitter_px: float  # spread of adopters sharing one location (pixels at width 960)
    map_width: int  # map width in pixels at the default image width of 960
    stats_at: tuple[float, float]  # where the date and count go, as fractions of width/height


# USA Contiguous Albers Equal Area Conic (EPSG:5070) parallels and origin.
US = Country(
    code="us",
    lat_range=(24.0, 50.0),
    lon_range=(-125.0, -66.0),
    top_lat=49.0,
    parallels=(29.5, 45.5),
    origin=(23.0, -96.0),
    periods_per_year=1,
    title="Semaglutide (Ozempic, Rybelsus, Wegovy) spreading through US prescribers",
    subtitle="Each light is a Medicare Part D prescriber; "
    "it comes on in the first year they have 11+ semaglutide claims",
    unit="prescribers",
    prevalence_text="{label}'s diabetes-drug prescribers had adopted by year end",
    source="Data: CMS Medicare Part D Prescribers, {start}-{end}. "
    "Contiguous US. Yearly data: timing within each year is illustrative.",
    light_gain=3.0,
    jitter_px=0.8,
    map_width=920,
    stats_at=(0.025, 0.745),
)

# The UK from the Isles of Scilly to Shetland, Northern Ireland included.
UK = Country(
    code="uk",
    lat_range=(49.8, 61.0),
    lon_range=(-8.3, 2.0),
    top_lat=61.0,
    parallels=(51.0, 58.0),
    origin=(54.5, -3.0),
    periods_per_year=12,
    title="Semaglutide (Ozempic, Rybelsus, Wegovy) spreading through UK GP practices",
    subtitle="Each light is a GP practice; "
    "it comes on once it reaches 11+ semaglutide items in 12 months",
    unit="GP practices",
    prevalence_text="practices prescribing diabetes drugs\nhad adopted by month end",
    source="Data: NHSBSA (England), Public Health Scotland, BSO (Northern Ireland), "
    "NWSSP (Wales), {start} to {end}.",
    light_gain=13.0,
    jitter_px=0.6,
    map_width=600,
    stats_at=(0.64, 0.42),
)

COUNTRIES = {c.code: c for c in (US, UK)}


@dataclass
class MapData:
    adopter_x: np.ndarray  # pixel column of each adopter
    adopter_y: np.ndarray  # pixel row
    adopter_time: np.ndarray  # adoption moment in years, e.g. 2019.37
    at_risk: dict[int, tuple[np.ndarray, np.ndarray]]  # period -> (x, y) of those at risk
    first_period: int  # periods count from year 0: a year (US), or year * 12 + month - 1 (UK)
    last_period: int
    prevalence_pct: dict[int, float]  # period -> % of population at risk that has adopted


def albers(
    lat: np.ndarray, lon: np.ndarray, country: Country = US
) -> tuple[np.ndarray, np.ndarray]:
    """Albers equal-area conic projection on a unit sphere: (x, y), y pointing north."""
    phi1, phi2 = np.radians(country.parallels)
    phi0, lam0 = np.radians(country.origin)
    phi, lam = np.radians(lat), np.radians(lon)
    n = (np.sin(phi1) + np.sin(phi2)) / 2
    c = np.cos(phi1) ** 2 + 2 * n * np.sin(phi1)
    rho0 = np.sqrt(c - 2 * n * np.sin(phi0)) / n
    rho = np.sqrt(c - 2 * n * np.sin(phi)) / n
    theta = n * (lam - lam0)
    return rho * np.sin(theta), rho0 - rho * np.cos(theta)


class PixelGrid:
    """Turns latitude/longitude into pixel positions: a map `map_width` pixels wide, drawn
    `margin` pixels from the left and `top` pixels from the top of an image `width` wide."""

    def __init__(
        self,
        width: int,
        margin: int,
        top: int,
        country: Country = US,
        map_width: int | None = None,
    ):
        # Project the edges of the bounding box to find the map's extent.
        (lat0, lat1), (lon0, lon1) = country.lat_range, country.lon_range
        lats, lons = np.linspace(lat0, lat1, 50), np.linspace(lon0, lon1, 50)
        edge_lat = np.concatenate([lats, lats, np.full(50, lat0), np.full(50, country.top_lat)])
        edge_lon = np.concatenate([np.full(50, lon0), np.full(50, lon1), lons, lons])
        x, y = albers(edge_lat, edge_lon, country)
        self.country = country
        self.x_min, self.y_max = x.min(), y.max()
        map_width = map_width or width - 2 * margin
        self.scale = map_width / (x.max() - x.min())
        self.margin, self.top = margin, top
        self.width = width
        self.height = round((y.max() - y.min()) * self.scale) + top + margin

    def to_pixels(self, lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x, y = albers(lat, lon, self.country)
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
    """Small fixed offsets (pixels), so adopters sharing a location form a cluster of lights
    rather than a single pixel."""
    u1 = np.clip(stable_fraction(keys, "jx"), 1e-9, None)
    u2 = stable_fraction(keys, "jy")
    radius = spread * np.sqrt(-2 * np.log(u1))  # Box-Muller: normally distributed offsets
    return radius * np.cos(2 * np.pi * u2), radius * np.sin(2 * np.pi * u2)


def period_label(period: int, country: Country) -> str:
    """2019 -> "2019" (US); the UK's month periods -> "Aug 2019"."""
    if country.periods_per_year == 1:
        return str(period)
    year, month = divmod(period, 12)
    return f"{calendar.month_abbr[month + 1]} {year}"


def place(
    rows: list[tuple], grid: PixelGrid, spread: float
) -> tuple[list[str], np.ndarray, np.ndarray]:
    """(id, latitude, longitude, ...) rows -> ids and jittered pixel positions."""
    ids = [str(r[0]) for r in rows]
    col, row = grid.to_pixels(np.array([r[1] for r in rows]), np.array([r[2] for r in rows]))
    dx, dy = jitter(ids, spread)
    return ids, col + dx, row + dy


def box_filter(country: Country) -> str:
    return (
        f"latitude between {country.lat_range[0]} and {country.lat_range[1]} "
        f"and longitude between {country.lon_range[0]} and {country.lon_range[1]}"
    )


def load_us(con: duckdb.DuckDBPyConnection, grid: PixelGrid, spread: float) -> MapData:
    box = box_filter(US)
    adopters = con.execute(
        f"select npi, latitude, longitude, adoption_year from us_adopters where {box} order by npi"
    ).fetchall()
    npis, x, y = place(adopters, grid, spread)
    years = np.array([r[3] for r in adopters], dtype=np.float64)

    at_risk = {}
    for (year,) in con.execute(
        "select distinct year from us_population_at_risk order by 1"
    ).fetchall():
        rows = con.execute(
            f"select npi, latitude, longitude from us_population_at_risk where year = ? and {box}",
            [year],
        ).fetchall()
        at_risk[year] = place(rows, grid, spread)[1:]

    prevalence = con.execute("select year, prevalence_pct from rpt_us_adoption_curve").fetchall()
    return MapData(
        adopter_x=x,
        adopter_y=y,
        adopter_time=years + stable_fraction(npis, "t"),
        at_risk=at_risk,
        first_period=min(at_risk),
        last_period=max(at_risk),
        prevalence_pct={int(k): float(v) for k, v in prevalence},
    )


# The UK's adoption month for the map: matched to the US rule (see uk_adopters), so the two
# maps can be read side by side. Practices that never reached 11+ items have no light.
ADOPTION = "matched_adoption_month"


def load_uk(con: duckdb.DuckDBPyConnection, grid: PixelGrid, spread: float) -> MapData:
    """All four nations, up to the last month that every nation has published (so the
    count and the percentage don't dip when one nation's latest month is missing)."""
    box = box_filter(UK)
    period = "(year(month) * 12 + month(month) - 1)"
    first, last = con.execute(
        f"select min(first), min(last) from (select min({period}) as first, "
        f"max({period}) as last from uk_population_at_risk group by nation)"
    ).fetchone()
    adopters = con.execute(
        f"select nation || ':' || practice_code, latitude, longitude, "
        f"year({ADOPTION}) * 12 + month({ADOPTION}) - 1 from uk_adopters "
        f"where {box} and year({ADOPTION}) * 12 + month({ADOPTION}) - 1 <= ? "
        "order by 1",
        [last],
    ).fetchall()
    ids, x, y = place(adopters, grid, spread)
    months = np.array([r[3] for r in adopters], dtype=np.float64)

    rows_by_period: dict[int, list[tuple]] = {}
    for row in con.execute(
        f"select nation || ':' || practice_code, latitude, longitude, {period} "
        f"from uk_population_at_risk where {box} and {period} <= ? order by 4, 1",
        [last],
    ).fetchall():
        rows_by_period.setdefault(row[3], []).append(row)
    at_risk = {p: place(rows, grid, spread)[1:] for p, rows in rows_by_period.items()}

    prevalence = con.execute(
        f"select {period}, 100.0 * sum(matched_adopted_at_risk) / sum(at_risk) "
        "from rpt_uk_adoption_curve group by 1"
    ).fetchall()
    return MapData(
        adopter_x=x,
        adopter_y=y,
        adopter_time=(months + stable_fraction(ids, "t")) / 12,
        at_risk=at_risk,
        first_period=first,
        last_period=last,
        prevalence_pct={int(k): float(v) for k, v in prevalence if k <= last},
    )


LOADERS: dict[str, Callable[[duckdb.DuckDBPyConnection, PixelGrid, float], MapData]] = {
    "us": load_us,
    "uk": load_uk,
}


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


def period_of(t: float, country: Country, data: MapData) -> int:
    """The period a frame belongs to. Frame times are period ends (2019.0 closes 2018)."""
    return min(int(t * country.periods_per_year - 1e-9), data.last_period)


def render_frame(
    data: MapData, grid: PixelGrid, t: float, show_at_risk: bool = False
) -> np.ndarray:
    """One RGB frame (uint8) at time t (in years)."""
    shape = (grid.height, grid.width)
    gain = grid.country.light_gain

    at_risk = np.zeros(shape)
    if show_at_risk:
        rx, ry = data.at_risk[period_of(t, grid.country, data)]
        at_risk = tone(glow(accumulate(rx, ry, np.ones_like(rx), shape), 1) * gain, 0.2)

    weights = light_curve(t - data.adopter_time)
    lit = weights > 0
    adopters = accumulate(data.adopter_x[lit], data.adopter_y[lit], weights[lit], shape)
    core = tone(glow(adopters, 1) * gain, 0.3)
    halo = tone(glow(adopters, 4) * gain, 0.06)

    warm = np.clip(0.8 * core + 0.35 * halo, 0, 1)[..., None]
    hot = np.clip(core - 0.75, 0, 1)[..., None] / 0.25
    rgb = BACKGROUND + at_risk[..., None] * COOL * (1 - warm)
    rgb = rgb + warm * (WARM - rgb)
    rgb = rgb + hot * (HOT - rgb)
    return np.clip(rgb, 0, 255).astype(np.uint8)


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def format_pct(pct: float) -> str:
    """One decimal for small shares and for shares just short of 100% (99.8% isn't 100%)."""
    return f"{pct:.1f}" if pct < 10 or 99.5 <= pct < 100 else f"{pct:.0f}"


def annotate(
    frame: np.ndarray, data: MapData, country: Country, t: float, adopted: int
) -> Image.Image:
    """Add the title, the date, the running count and the source line."""
    image = Image.fromarray(frame)
    draw = ImageDraw.Draw(image)
    w, h = image.size
    s = w / 960  # text scales with image width
    pad = int(24 * s)
    draw.text((pad, pad), country.title, font=font(int(22 * s)), fill=TEXT)
    draw.text((pad, pad + int(30 * s)), country.subtitle, font=font(int(13 * s)), fill=SUBTLE)

    period = period_of(t, country, data)
    label = period_label(period, country)
    x, y = int(country.stats_at[0] * w), int(country.stats_at[1] * h)
    draw.text((x, y), label, font=font(int(64 * s)), fill=TEXT)
    draw.text(
        (x, y + int(72 * s)), f"{adopted:,} {country.unit}", font=font(int(20 * s)), fill=WARM_TEXT
    )
    pct = data.prevalence_pct.get(period)
    if pct is not None:
        shown = format_pct(pct)
        draw.text(
            (x, y + int(98 * s)),
            f"{shown}% of {country.prevalence_text.format(label=label)}",
            font=font(int(13 * s)),
            fill=SUBTLE,
        )
    source = country.source.format(
        start=period_label(data.first_period, country),
        end=period_label(data.last_period, country),
    )
    draw.text((pad, h - int(24 * s)), source, font=font(int(11 * s)), fill=SUBTLE)
    return image


def frame_times(start: float, end: float, per_year: int) -> list[float]:
    """Frame times (in years) from `start` to `end`: each frame shows the moment it ends.
    Evenly spaced, as close to `per_year` a year as fits (8 years 7 months at 10 a year: 86
    frames), with the last frame exactly at `end`."""
    steps = max(1, round((end - start) * per_year))
    return [start + (i + 1) * (end - start) / steps for i in range(steps)]


def to_palette(frames: list[Image.Image], colors: int = COLORS) -> list[Image.Image]:
    """Convert every frame to one shared palette (GIFs allow up to 256 colours), so colours
    don't flicker between frames."""
    # Build the palette from a strip of frames across the animation, so the early blues are
    # in it as well as the late ambers.
    picks = [*frames[:: max(1, len(frames) // 6)], frames[-1]]
    w, h = frames[0].size
    strip = Image.new("RGB", (w, h * len(picks)))
    for i, frame in enumerate(picks):
        strip.paste(frame, (0, i * h))
    reference = strip.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
    return [f.quantize(palette=reference, dither=Image.Dither.NONE) for f in frames]


def render(
    con: duckdb.DuckDBPyConnection,
    country: Country = US,
    out_dir: Path = IMAGES_DIR,
    width: int = WIDTH,
    frames_per_year: int = 10,
    frame_ms: int = 120,
    hold_ms: int = 4000,
    show_at_risk: bool = False,
) -> tuple[Path, Path]:
    """Draw every frame from the warehouse behind `con`; save the GIF and the final PNG.
    Both maps have the same pace by default: 10 frames a year, 120 ms each (1.2 s a year)."""
    s = width / 960  # layouts are designed at 960 pixels wide and scaled
    grid = PixelGrid(
        width,
        margin=int(20 * s),
        top=int(70 * s),
        country=country,
        map_width=int(country.map_width * s),
    )
    data = LOADERS[country.code](con, grid, country.jitter_px * s)
    log.info("%s: %s adopters on the map", country.code, f"{len(data.adopter_time):,}")

    ppy = country.periods_per_year
    frames = []
    for t in frame_times(data.first_period / ppy, (data.last_period + 1) / ppy, frames_per_year):
        adopted = int((data.adopter_time <= t).sum())
        frame = render_frame(data, grid, t, show_at_risk)
        frames.append(annotate(frame, data, country, t, adopted))

    out_dir.mkdir(parents=True, exist_ok=True)
    year, month = divmod(data.last_period, country.periods_per_year)
    end = str(year) if country.periods_per_year == 1 else f"{year}-{month + 1:02d}"
    png_path = out_dir / f"{country.code}_night_lights_{end}.png"
    gif_path = out_dir / f"{country.code}_night_lights.gif"
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
    parser.add_argument("--country", choices=[*COUNTRIES, "all"], default="all")
    parser.add_argument("--width", type=int, default=WIDTH, help="image width in pixels")
    parser.add_argument(
        "--frames-per-year", type=int, default=10, help="the same for both maps, so they keep pace"
    )
    parser.add_argument(
        "--show-population-at-risk",
        action="store_true",
        help="also draw everyone prescribing diabetes drugs as a faint blue speck",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    countries = COUNTRIES.values() if args.country == "all" else [COUNTRIES[args.country]]
    with duckdb.connect(str(WAREHOUSE_PATH), read_only=True) as con:
        for country in countries:
            render(
                con,
                country,
                width=args.width,
                frames_per_year=args.frames_per_year,
                show_at_risk=args.show_population_at_risk,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
