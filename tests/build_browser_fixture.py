"""Build a small real export for CI browser checks without production data."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_browsing import BrowsingTests
import publish
from scripts.prepare_pages import prepare

case = BrowsingTests()
try:
    case.setUp()
    exported = Path(case.directory.name) / 'export'
    publish.publish(case.path, exported)
    prepare(exported, sys.argv[1])
finally:
    case.doCleanups()
