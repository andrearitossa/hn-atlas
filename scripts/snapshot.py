"""Create a consistent SQLite snapshot while the live database is in WAL mode."""
import argparse
import os
import sqlite3
from pathlib import Path


def snapshot(source, destination):
    destination = Path(destination)
    temp = destination.with_suffix(destination.suffix + '.partial')
    if destination.exists() or temp.exists():
        raise FileExistsError(destination)
    try:
        with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as live:
            with sqlite3.connect(temp) as copy:
                live.backup(copy, pages=1000, sleep=0.1)
                if copy.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Snapshot failed SQLite quick_check')
        os.replace(temp, destination)
    finally:
        if temp.exists():
            temp.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    args = parser.parse_args()
    snapshot(args.source, args.destination)
