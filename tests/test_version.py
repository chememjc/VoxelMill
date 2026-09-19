"""Package version is 0.x.x and the two source-of-truth files agree."""
import re
from pathlib import Path

from voxelmill import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_package_version_is_0_x_and_matches_pyproject():
    text = (ROOT / 'pyproject.toml').read_text()
    match = re.search(r'(?m)^version = "([^"]+)"', text)
    assert match is not None
    assert match.group(1) == __version__
    assert re.fullmatch(r'0\.\d+\.\d+', __version__), __version__
