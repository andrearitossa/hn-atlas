"""Export public data and the UI to a static site; no database is deployed."""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

import catalog
import timeline
from database import connect
import production

SOURCE = Path(__file__).resolve().parent


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':'),
                               allow_nan=False), encoding='utf-8')


def publish(database, output='dist'):
    output = Path(output)
    releases = output / 'releases'
    releases.mkdir(parents=True, exist_ok=True)
    version = uuid.uuid4().hex
    # Build privately, then switch the entry point only after every file is ready.
    with tempfile.TemporaryDirectory(prefix='.build-', dir=releases) as temporary:
        build = Path(temporary)
        (build / 'topics').mkdir()
        (build / 'stories').mkdir()
        (build / 'timelines').mkdir()
        with connect(database, readonly=True) as c:
            c.execute('BEGIN')  # One consistent snapshot, even during ingestion.
            overview = catalog.overview(c)
            overview['aliases'] = {
                str(row[0]): production.resolve_topic(c, row[0])
                for row in c.execute("SELECT id FROM topic_registry WHERE status='merged'")
            }
            write_json(build / 'topics.json', overview)
            write_json(build / 'timelines' / 'all.json', {days: timeline.build(c, days=days, as_of=overview['as_of']) for days in (30, 90, 365)})
            for item in overview['topics']:
                topic_id = item['id']
                write_json(build / 'timelines' / f'{topic_id}.json', {'zoom': timeline.zoom(c, topic_id, overview['as_of']), 0: timeline.history(c, topic_id, overview['as_of']), **{days: timeline.build(c, topic_id, days, overview['as_of']) for days in (30, 90, 365)}})
                detail = catalog.detail(c, topic_id, as_of=overview['as_of'])
                detail['digests'] = {
                    cadence: dict(id=topic_id, name=item['name'], cadence=cadence,
                                  as_of=overview['as_of'], posts=production.ranked_posts(
                                      c, topic_id, cadence, now=overview['as_of']))
                    for cadence in ('weekly', 'monthly')
                }
                write_json(build / 'topics' / f'{topic_id}.json', detail)
                posts = [dict(row) for row in c.execute(
                    f'SELECT {catalog.POST} FROM story_topics st JOIN stories s USING(id) '
                    'WHERE st.topic=? AND s.dead=0 AND s.deleted=0 AND s.time<=? '
                    'ORDER BY s.time DESC,s.id DESC', (topic_id, overview['as_of']))]
                write_json(build / 'stories' / f'{topic_id}.json', posts)
        shutil.copyfile(SOURCE / 'data.js', build / 'data.js')
        html = (SOURCE / 'index.html').read_text(encoding='utf-8')
        root = f'./releases/{version}/'
        html = html.replace('name="hn-data" content=""', f'name="hn-data" content="{root}"')
        html = html.replace('src="./data.js"', f'src="{root}data.js"')
        (build / 'index.html').write_text(html, encoding='utf-8')
        # Keep previous releases so tabs opened before publication still work.
        release = releases / version
        os.replace(build, release)
        os.replace(release / 'index.html', output / 'index.html')
    return output / 'index.html'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=os.getenv('HN_DB', 'data/hackernews.db'))
    parser.add_argument('--output', default='dist')
    args = parser.parse_args()
    print(f'Published {publish(args.db, args.output)}')
