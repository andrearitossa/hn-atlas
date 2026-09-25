"""Run the weekly data pipeline and newsletter delivery once. Schedule externally."""
import argparse
import logging
import os
import sqlite3

import weekly
import fcntl
import production
from publish import publish

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


def run(output=None):
    db = os.getenv('HN_DB', 'data/hackernews.db')
    with open(db+'.worker.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return run_locked(db,output)


def run_locked(db,output=None):
    failed = False
    try:
        weekly.run(db)
        if output:
            logging.info('Published %s', publish(db, output))
    except Exception:
        failed = True
        logging.exception('Weekly data pipeline or static publication failed')
        # Delivery is independent of a transient HN/OpenAI failure.
    if os.getenv('SMTP_HOST') and os.getenv('SMTP_FROM'):
        try:
            with sqlite3.connect(db, timeout=30) as conn:
                sent = production.deliver(conn, os.environ['PUBLIC_URL'].rstrip('/'))
                logging.info('Sent %s digests', sent)
        except Exception:
            logging.exception('Newsletter delivery failed')
            raise
    if failed:
        raise RuntimeError('Weekly data pipeline failed; see earlier log')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', metavar='DIRECTORY', help='Export the static site after a successful update')
    run(parser.parse_args().publish)
