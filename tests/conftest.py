import shutil
import uuid
from pathlib import Path

import pytest

from philosophy_frontier_monitor.taxonomy import load_taxonomy

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def taxonomy():
    return load_taxonomy(FIXTURE_DIR / "philpapers_categories_small.json")


@pytest.fixture
def workspace_tmp_path():
    path = Path(__file__).parents[1] / "var" / "test-pytest" / uuid.uuid4().hex
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path)
