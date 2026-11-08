import subprocess

import semaglutide_spread
from semaglutide_spread import paths


def test_package_has_version():
    assert semaglutide_spread.__version__ == "0.1.0"


def test_project_root_is_the_repo():
    assert (paths.PROJECT_ROOT / "pyproject.toml").is_file()


def test_data_paths_live_under_data_dir():
    for path in (paths.RAW_DIR, paths.OUTPUT_DIR, paths.WAREHOUSE_PATH):
        assert path.is_relative_to(paths.DATA_DIR)


def test_data_is_ignored_by_git():
    """Data files must never be committed: they are large and rebuilt by the pipeline."""
    for path in (paths.RAW_DIR / "example.parquet", paths.WAREHOUSE_PATH):
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", str(path)],
            cwd=paths.PROJECT_ROOT,
            check=False,
        )
        assert result.returncode == 0, f"{path} is not git-ignored"
