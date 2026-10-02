"""Build a small real export for CI browser checks without production data."""
from pathlib import Path
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_browsing import BrowsingTests
import publish
from database import connect
from scripts.prepare_pages import prepare

case = BrowsingTests()
try:
    case.setUp()
    # Keep the default completed month populated even near month boundaries.
    start = datetime.fromtimestamp(case.clock, timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with connect(case.path) as connection:
        connection.execute("UPDATE topics SET description='Rust programming and compilers' WHERE id=3")
        connection.execute('UPDATE stories SET time=? WHERE id=3', (int(start.timestamp()) - 86400,))
    exported = Path(case.directory.name) / 'export'
    publish.publish(case.path, exported)
    prepare(exported, sys.argv[1])
finally:
    case.doCleanups()
