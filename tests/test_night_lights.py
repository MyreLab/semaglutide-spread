import numpy as np
import pytest

from semaglutide_spread.viz import night_lights as nl


def test_projection_origin_is_zero_and_north_is_up():
    x, y = nl.albers(np.array([23.0, 40.0]), np.array([-96.0, -96.0]))
    assert x[0] == pytest.approx(0) and y[0] == pytest.approx(0)
    assert x[1] == pytest.approx(0) and y[1] > 0


def test_east_coast_is_right_of_west_coast():
    grid = nl.PixelGrid(960, margin=20, top=70)
    col, row = grid.to_pixels(np.array([37.8, 40.7]), np.array([-122.3, -74.0]))  # SF, NYC
    assert 20 <= col[0] < col[1] <= 940
    assert row.min() >= 70 and row.max() <= grid.height


def test_a_light_flashes_then_settles():
    age = np.array([-0.1, 0.0, nl.FLASH_LENGTH / 2, nl.FLASH_LENGTH, 3.0])
    off, peak, fading, steady, later = nl.light_curve(age)
    assert off == 0
    assert peak == pytest.approx(nl.FLASH_PEAK)
    assert 1 < fading < peak
    assert steady == later == 1


def test_frames_run_to_the_end_of_the_last_year():
    times = nl.frame_times(2018, 2020, per_year=4)
    assert len(times) == 12
    assert times[0] == pytest.approx(2018.25) and times[-1] == pytest.approx(2021.0)


def test_box_blur_spreads_light_without_adding_any():
    image = np.zeros((21, 21))
    image[10, 10] = 9.0
    blurred = nl.glow(image, 1)
    assert blurred.sum() == pytest.approx(9.0)
    assert blurred[10, 10] < 9.0 and blurred[10, 11] > 0


def test_fixed_random_numbers_repeat():
    a = nl.stable_fraction(["1", "2", "3"], "t")
    assert (a == nl.stable_fraction(["1", "2", "3"], "t")).all()
    assert ((a >= 0) & (a < 1)).all()
