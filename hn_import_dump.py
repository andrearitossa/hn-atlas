#!/usr/bin/env python3
"""Import the open-index/hacker-news Parquet dump (data/hn_dump/data/*/*.parquet)
into the `stories` table of data/hackernews.db.

Only stories, polls and jobs are kept; comments and poll options are skipped.
Rows already in the table (fetched live from the API) are left untouched.

    .venv/bin/python hn_import_dump.py [--dump data/hn_dump] [--db data/hackernews.db]
"""
import argparse
import glob
import os
import time

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from hn_sync import open_db

TYPE_NAMES = {1: "story", 3: "poll", 5: "job"}
COLUMNS = ["id", "type", "by", "time", "title", "url", "text",
           "score", "descendants", "dead", "deleted"]


def import_file(conn, path: str, now: int) -> int:
    table = pq.read_table(path, columns=COLUMNS)
    table = table.filter(pc.is_in(
        table["type"], value_set=pa.array(list(TYPE_NAMES), table["type"].type)))
    if table.num_rows == 0:
        return 0
    epoch = pc.divide(pc.cast(table["time"], "int64"), 1000)  # ms -> s
    cols = table.to_pydict()
    rows = [
        (
            cols["id"][i], TYPE_NAMES[cols["type"][i]], cols["by"][i] or None, t,
            cols["title"][i] or None, cols["url"][i] or None, cols["text"][i] or None,
            cols["score"][i], cols["descendants"][i],
            int(bool(cols["dead"][i])), int(bool(cols["deleted"][i])), now,
        )
        for i, t in enumerate(epoch.to_pylist())
    ]
    conn.executemany(
        """INSERT INTO stories (id, type, by, time, title, url, text, score,
                                descendants, dead, deleted, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO NOTHING""",
        rows,
    )
    conn.commit()
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", default="data/hn_dump")
    ap.add_argument("--db", default="data/hackernews.db")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dump, "data", "*", "*.parquet")))
    if not files:
        ap.error(f"no Parquet files found under {args.dump}/data/*/")
    conn = open_db(args.db)
    now, total, t0 = int(time.time()), 0, time.time()
    for n, path in enumerate(files, 1):
        total += import_file(conn, path, now)
        print(f"[{n}/{len(files)}] {os.path.basename(path)}  {total:,} articles "
              f"({time.time() - t0:.0f}s)", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
