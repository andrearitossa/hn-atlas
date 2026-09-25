"""Score a pre-labelled real-story routing fixture. --review makes one model call.

This is a targeted regression benchmark, not an unbiased accuracy estimate.
Requires the matching legacy registry/story snapshot used by the fixture.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import embed  # Loads the existing local API configuration without displaying it.
from routing import semantic_decisions


def evaluate(db,cases,review=False):
    cases=json.loads(Path(cases).read_text())
    if not cases or len(cases)>100:
        raise ValueError('Use between 1 and 100 pre-labelled stories')
    conn=sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    try:
        topics=[dict(r) for r in conn.execute("SELECT t.id,t.name,t.description FROM topics t JOIN topic_registry r ON r.id=t.id WHERE r.status='active'")]
        stories=[dict(conn.execute('SELECT s.id,s.title,s.url,st.topic AS current_topic FROM stories s JOIN story_topics st USING(id) WHERE s.id=?',(case['id'],)).fetchone()) for case in cases]
        result={'total':len(cases),'baseline_correct':sum(s['current_topic'] in c['acceptable'] for s,c in zip(stories,cases))}
        if review:
            decisions=semantic_decisions(topics,stories)
            chosen={r['id']:r['topic'] for r in decisions}
            result.update(review_correct=sum(chosen[c['id']] in c['acceptable'] for c in cases),decisions=decisions)
        return result
    finally:conn.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default='data/hackernews.db')
    parser.add_argument('--cases',default='tests/fixtures/topic_routing.json')
    parser.add_argument('--review',action='store_true')
    args=parser.parse_args()
    print(json.dumps(evaluate(args.db,args.cases,args.review),indent=2))
