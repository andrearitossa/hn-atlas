"""Initialize Search history and expire raw events after 90 days. No post changes."""
import json
from pathlib import Path
import sys
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from search_sync import Cloudflare


def main():
    cloud = Cloudflare(json.loads((ROOT/'search-cloudflare.json').read_text()))
    cloud.sql((ROOT/'migrations/search/0002_search_history.sql').read_text())
    expiry = int(time.time()) - 90 * 86400
    cloud.sql(f'DELETE FROM searches WHERE created_at < {expiry}')
    print('Search history ready; 90-day retention applied.')


if __name__ == '__main__':
    main()
