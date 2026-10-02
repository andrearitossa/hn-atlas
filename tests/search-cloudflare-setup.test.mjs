import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
test('Daily Search sync precedes deployment and requires configured stores',()=>{
 const script=readFileSync('scripts/update_site.sh','utf8');
 assert.ok(script.indexOf('search_sync.py')<script.indexOf('bash scripts/deploy_site.sh'));
 assert.match(script,/if \[\[ -f search-cloudflare\.json \]\]/);
});
