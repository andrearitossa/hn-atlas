"""Export current inputs, assignments and featured picks for manual review. No API calls."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import attention
import routing
from config import DB
from database import connect


def review():
    with connect(DB, readonly=True) as conn:
        now = conn.execute("SELECT value FROM maintenance WHERE key='refresh'").fetchone()[0]
        base = '''SELECT s.id,s.title,s.url,s.text,s.score,s.time,st.topic,t.name,
                  q.reason FROM stories s
                  LEFT JOIN story_topics st USING(id) LEFT JOIN topics t ON st.topic=t.id
                  LEFT JOIN classification_queue q USING(id)
                  JOIN routing_reviews r USING(id)
                  WHERE r.reviewed_at=? AND s.dead=0 AND s.deleted=0'''
        samples = {}
        for label, condition in [('popular', ''), ('post_body', " AND coalesce(s.text,'')!=''"),
                                 ('unassigned', ' AND st.topic IS NULL')]:
            rows = conn.execute(base + condition + ' ORDER BY s.score DESC,s.id DESC LIMIT 20', (now,))
            samples[label] = [dict(row, embedding_input=routing.subject_text(row['title'],row['url'],row['text'])) for row in rows]
        topic_ids = {row['topic'] for rows in samples.values() for row in rows if row['topic'] is not None}
        samples['featured'] = {str(topic): attention.trending(conn, topic, now, 3) for topic in sorted(topic_ids)}
        samples['as_of'] = now
    output = Path('report/pipeline-review.json')
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(samples, indent=2, ensure_ascii=False))
    print(output)


if __name__ == '__main__':
    review()
