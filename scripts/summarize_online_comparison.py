"""Read-only user-facing comparison measurements; no semantic accuracy claims."""
from collections import Counter
import json
import os
from pathlib import Path
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'data'/os.getenv('COMPARISON_RUN','comparison-online-v2')
OUT=ROOT/'report'/RUN.name
OUT.mkdir(exist_ok=True,parents=True)


def main():
    manifest=json.loads((RUN/'manifest.json').read_text());end=manifest['end'];since=end-90*86400
    result=dict(interval=manifest['static_interval'],split=manifest['split'],recent_since=since)
    for label in ('static','stream'):
        conn=sqlite3.connect((RUN/f'{label}.db').resolve().as_uri()+'?mode=ro',uri=True)
        total=conn.execute('SELECT count(*) FROM stories WHERE time>=? AND time<? AND dead=0 AND deleted=0',(since,end)).fetchone()[0]
        assigned=conn.execute('SELECT count(*) FROM story_topics st JOIN stories s USING(id) WHERE s.time>=? AND s.time<? AND s.dead=0 AND s.deleted=0',(since,end)).fetchone()[0]
        notable=conn.execute('''SELECT count(*),sum(st.id IS NOT NULL) FROM stories s LEFT JOIN story_topics st USING(id)
            WHERE s.time>=? AND s.time<? AND s.dead=0 AND s.deleted=0 AND (s.score>=10 OR s.descendants>=5)''',(since,end)).fetchone()
        topics=[dict(zip(('id','name','description'),r)) for r in conn.execute("SELECT t.id,name,description FROM topics t JOIN topic_registry r USING(id) WHERE r.status='active'")]
        counts=dict(conn.execute('SELECT topic,count(*) FROM story_topics st JOIN stories s USING(id) WHERE s.time>=? AND s.time<? GROUP BY topic',(since,end)))
        result[label]=dict(topics=len(topics),recent_stories=total,assigned_recent=assigned,
            recent_coverage=assigned/total,notable_stories=notable[0],assigned_notable=notable[1],
            notable_coverage=(notable[1] or 0)/max(1,notable[0]),topics_with_no_recent_posts=sum(counts.get(t['id'],0)==0 for t in topics),
            topics_with_under_10_recent_posts=sum(counts.get(t['id'],0)<10 for t in topics))
        (OUT/f'{label}-list.md').write_text(f'# {label.title()} topics\n\n'+ '\n'.join(
            f"- **{t['name']}** ({counts.get(t['id'],0)} recent stories): {t['description']}" for t in sorted(topics,key=lambda t:t['name'])))
        if label=='stream':
            conn.execute('ATTACH DATABASE ? AS baseline',((RUN/'static.db').resolve().as_uri()+'?mode=ro',))
            # Story overlap aligns taxonomies without incorrectly comparing vectors
            # centered using two different historical means.
            overlaps=conn.execute('''SELECT a.topic,b.topic,count(*) FROM story_topics a
                JOIN baseline.story_topics b USING(id) JOIN stories s USING(id)
                WHERE s.time>=? AND s.time<? GROUP BY a.topic,b.topic''',(since,end)).fetchall()
            lookup={t['id']:t['name'] for t in topics}
            static_names=dict(conn.execute('SELECT id,name FROM baseline.topics'))
            result['overlap']=[dict(online=lookup[a],static=static_names[b],stories=n) for a,b,n in sorted(overlaps,key=lambda r:-r[2])]
            gaps=conn.execute("""SELECT s.id,s.title,s.score,s.descendants,b.topic,q.suggested_topic,q.sim,q.margin
                FROM stories s JOIN baseline.story_topics b USING(id)
                LEFT JOIN story_topics a USING(id) LEFT JOIN classification_queue q USING(id)
                WHERE s.time>=? AND s.time<? AND a.id IS NULL AND (s.score>=10 OR s.descendants>=5)
                ORDER BY coalesce(s.score,0)+2*coalesce(s.descendants,0) DESC LIMIT 40""",(since,end)).fetchall()
            result['notable_static_only_examples']=[dict(id=r[0],title=r[1],score=r[2],comments=r[3],
                static_topic=static_names[r[4]],online_suggestion=lookup.get(r[5]),fit=r[6],margin=r[7]) for r in gaps]
            reviews=[]
            for at,details in conn.execute("SELECT at,details FROM topic_changes WHERE kind='review' ORDER BY at"):
                review=json.loads(details)
                reviews.append(dict(at=at,events=review['events'],rejected=review.get('rejected',[]),concerns=review['plan'].get('concerns',[]),plan=review['plan']))
            (OUT/'reviews.json').write_text(json.dumps(reviews,indent=2))
            result['review_count']=len(reviews)
            result['event_counts']=dict(Counter(e[0] for r in reviews for e in r['events']))
            result['rejected_proposals']=sum(len(r['rejected']) for r in reviews)
            seed=json.loads((RUN/'stream-initial-topics.json').read_text())
            names={t['id']:[t['name']] for t in seed}
            for review in reviews:
                rejected=[r['change'] for r in review['rejected']]
                for change in review['plan']['changes']:
                    if change in rejected:
                        continue
                    for subject in change['subjects']:
                        ident=subject['id']
                        if ident is not None:
                            history=names.setdefault(ident,[])
                            if not history or history[-1]!=subject['name']:
                                history.append(subject['name'])
            (OUT/'name-history.json').write_text(json.dumps(names,indent=2))
            result['seed_name_changes']=[dict(id=t['id'],initial=t['name'],final=lookup.get(t['id']),
                distinct_names=len(set(names[t['id']]))) for t in seed if len(names[t['id']])>1 or t['id'] not in lookup]
        conn.close()
    weeks=[json.loads(line) for line in (RUN/'weeks.jsonl').read_text().splitlines()]
    result['weeks']=len(weeks);result['replay_seconds']=sum(w['seconds'] for w in weeks)
    (OUT/'metrics.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('overlap','notable_static_only_examples')},indent=2))


if __name__=='__main__':
    main()
