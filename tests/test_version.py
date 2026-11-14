"""One version number, everywhere it's written."""

import json
from pathlib import Path
import re

from custom_components.hearth.const import VERSION

ROOT = Path(__file__).parents[1] / "custom_components" / "hearth"


def test_versions_match() -> None:
    assert json.loads((ROOT / "manifest.json").read_text())["version"] == VERSION
    for js in sorted((ROOT / "frontend").glob("*.js")):
        text = js.read_text()
        for found in re.findall(r'VERSION = "([^"]+)"', text):
            assert found == VERSION, js.name
        for found in re.findall(r"\?v=([0-9][^\"'`]*)", text):
            assert found == VERSION, js.name
