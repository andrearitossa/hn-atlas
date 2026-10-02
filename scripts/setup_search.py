"""Create dedicated Search stores and configure Pages bindings. Does not deploy."""
import json
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from search_sync import Cloudflare

ACCOUNT = 'c2e5e5e106e0b8c69e797dd75d0d229d'
INDEX = 'hackeratlas-search'
NAME = 'hackeratlas-search'


def setup():
    cloud = Cloudflare({'account_id': ACCOUNT})
    # Check Vectorize access before creating anything or changing the daily pipeline.
    indexes = cloud.api('GET', '/vectorize/v2/indexes')
    if isinstance(indexes, dict):
        indexes = indexes.get('indexes', [])
    existing = next((i for i in indexes if i['name'] == INDEX), None)
    if not existing:
        existing = cloud.api('POST', '/vectorize/v2/indexes', json={'name': INDEX, 'config': {'dimensions': 512, 'metric': 'cosine'}})
    if existing.get('config', {}).get('dimensions') != 512 or existing.get('config', {}).get('metric') != 'cosine':
        raise RuntimeError('Existing Search vector index has incompatible dimensions or metric')
    # Metadata must be indexed before upserting the vectors it will filter.
    metadata = cloud.api('GET', f'/vectorize/v2/indexes/{INDEX}/metadata_index/list')
    if isinstance(metadata, dict):
        metadata = metadata.get('metadataIndexes', metadata.get('indexes', []))
    for field in ('topic1', 'topic2', 'topic3'):
        current = next((m for m in metadata if m['propertyName'] == field), None)
        if current and current['indexType'].lower() != 'number':
            raise RuntimeError(f'Incompatible metadata index: {field}')
        if not current:
            cloud.api('POST', f'/vectorize/v2/indexes/{INDEX}/metadata_index/create',
                      json={'propertyName': field, 'indexType': 'number'})
    databases = cloud.api('GET', '/d1/database')
    database = next((d for d in databases if d['name'] == NAME), None)
    if not database:
        database = cloud.api('POST', '/d1/database', json={'name': NAME, 'primary_location_hint': 'weur'})
    config = dict(account_id=ACCOUNT, database_id=database['uuid'], index_name=INDEX)
    wrangler_path = ROOT/'wrangler.jsonc'
    wrangler = json.loads(wrangler_path.read_text())
    wrangler['d1_databases'] = [d for d in wrangler.get('d1_databases', []) if d['binding'] != 'SEARCH_DB']
    wrangler['d1_databases'].append(dict(binding='SEARCH_DB', database_name=NAME, database_id=database['uuid'], migrations_dir='migrations/search'))
    wrangler['vectorize'] = [v for v in wrangler.get('vectorize', []) if v['binding'] != 'SEARCH_VECTORS']
    wrangler['vectorize'].append(dict(binding='SEARCH_VECTORS', index_name=INDEX))
    # Initialize before activating the daily sync configuration.
    cloud.config = config
    from search_sync import SCHEMA
    for migration in sorted(SCHEMA.parent.glob('*.sql')):
        cloud.sql(migration.read_text())
    wrangler_path.write_text(json.dumps(wrangler, indent=2)+'\n')
    (ROOT/'search-cloudflare.json').write_text(json.dumps(config, indent=2)+'\n')
    print('Search stores and Pages bindings configured. Run search_sync.py, then deploy.')


if __name__ == '__main__':
    setup()
