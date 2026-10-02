"""Apply Search history schema. No post changes."""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloudflare import Cloudflare


def main():
    cloud = Cloudflare(json.loads((ROOT/'search-cloudflare.json').read_text()))
    cloud.sql((ROOT/'migrations/search/0002_search_history.sql').read_text())
    print('Search history schema ready.')


if __name__ == '__main__':
    main()
