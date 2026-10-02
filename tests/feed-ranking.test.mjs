import test from 'node:test';
import assert from 'node:assert/strict';
import {rankFeed} from '../workers/feed/ranking.js';
const now = 1800000000;
const post = (id, topics, points = 10, age = 0, key = String(id)) => ({id, topics, hn_points:points, time:now - age, article_key:key});
test('personal feed matches topics, uses decayed popularity, excludes invalid ages, and deduplicates URLs', () => {
  const data = {as_of:now, posts:[post(1,[3],20), post(2,[3,4],100,4*86400), post(3,[4],1000),
    post(4,[3],30,0,'same'),post(5,[3],10,0,'same'),post(6,[3],100,-100),post(7,[3],100,31*86400),post(8,[3],4)]};
  assert.deepEqual(rankFeed(data,[3]).map(p => p.id),[4,2,1]);
  assert.deepEqual(rankFeed(data,[]),[]);
  assert.equal(rankFeed({as_of:now,posts:Array.from({length:80},(_,i)=>post(i,[3]))},[3]).length,80);
});
test('duplicate selection happens after filtering personal topics', () => {
  assert.deepEqual(rankFeed({as_of:now,posts:[post(1,[4],1000,0,'same'),post(2,[3],10,0,'same')]},[3]).map(p=>p.id),[2]);
});
