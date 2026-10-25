import json
from pathlib import Path

import pytest

from outdoorgraph.ir import Scene


@pytest.fixture
def demo_scene() -> Scene:
    path = Path(__file__).resolve().parents[1] / "configs" / "demo_valley.json"
    return Scene.from_dict(json.loads(path.read_text(encoding="utf-8")))
