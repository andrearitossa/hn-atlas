import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {cutoff} from '../functions/api/search/index.js';

test('search calendar cutoff matches the Python fixture',()=>{
  const cases=JSON.parse(readFileSync(new URL('./fixtures/corpus-cutoff.json',import.meta.url),'utf8'));
  for(const row of cases)assert.equal(cutoff(new Date(row.timestamp*1000)),row.cutoff);
});
