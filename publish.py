"""Export public data and the UI to a static site; no database is deployed."""
from config import DB

import argparse
import json
import os
import re
from pathlib import Path
import shutil
import tempfile
import uuid

import catalog
import attention
import analytics
import search_export
import timeline
from database import connect
import seo
import static_pages
SOURCE = Path(__file__).resolve().parent


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':'),
                               allow_nan=False), encoding='utf-8')


def static_html(template, overview, version, trends_data=None):
    html = seo.render(template, overview=overview)
    root = f'./releases/{version}/'
    html = html.replace('name="hn-data" content=""', f'name="hn-data" content="{root}"')
    html = html.replace('src="./data.js"', f'src="{root}data.js"')
    initial = {'topics.json': overview}
    payload = json.dumps(initial, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    html = html.replace('<script src=', f'<script type="application/json" id="hn-initial">{payload}</script>\n<script src=', 1)
    content = static_pages.home_content(template, overview)
    if trends_data is not None:
        content = re.sub(r'<div id="trends-mount">.*?</div>', lambda _: analytics.embedded(trends_data, SOURCE), content, count=1, flags=re.S)
    html = re.sub(r'<main id="app">.*?</main>', lambda _: f'<main id="app">{content}</main>', html, count=1, flags=re.S)
    html = html.replace('<body>', '<body class="connections-route browse-route">')
    # Static topic links use native document navigation, not the live API router.
    start = html.index('// ---------- topic page ----------')
    end = html.index('// Feedback stays available', start)
    html = html[:start] + 'function topic(id) { location.href = `/topic/${id}/`; }\n' + html[end:]
    html = html.replace('  event.preventDefault();\n  if (url.href !== location.href)',
                        '  if (url.pathname.startsWith("/topic/")) return;\n  event.preventDefault();\n  if (url.href !== location.href)')
    return static_pages.external_styles(html, root)


def publish(database, output='dist'):
    output = Path(output)
    releases = output / 'releases'
    releases.mkdir(parents=True, exist_ok=True)
    version = uuid.uuid4().hex
    # Build privately, then switch the entry point only after every file is ready.
    with tempfile.TemporaryDirectory(prefix='.build-', dir=releases) as temporary:
        build = Path(temporary)
        public = build / 'public'
        public.mkdir()
        static_pages.write_assets(build, SOURCE)
        template = (SOURCE / 'index.html').read_text(encoding='utf-8')
        (build / 'topics').mkdir()
        (build / 'stories').mkdir()
        (build / 'recent').mkdir()
        (build / 'timelines').mkdir()
        with connect(database, readonly=True) as c:
            c.execute('BEGIN')  # One consistent snapshot, even during ingestion.
            overview = catalog.overview(c)
            write_json(build / 'topics.json', overview)
            write_json(build / 'timelines' / 'all.json', {days: timeline.build(c, days=days, as_of=overview['as_of']) for days in (30, 90, 365)})
            for item in overview['topics']:
                topic_id = item['id']
                history = timeline.zoom(c, topic_id, overview['as_of'])
                write_json(build / 'timelines' / f'{topic_id}.json', {'zoom': history, 0: timeline.history(c, topic_id, overview['as_of']), **{days: timeline.build(c, topic_id, days, overview['as_of']) for days in (30, 90, 365)}})
                detail = catalog.detail(c, topic_id, as_of=overview['as_of'])
                write_json(build / 'topics' / f'{topic_id}.json', detail)
                page = public / 'topic' / detail['slug']
                page.mkdir(parents=True)
                posts = [dict(row) for row in c.execute(
                    f'SELECT {catalog.POST} FROM {catalog.topics.memberships(c)} st JOIN stories s USING(id) '
                    'WHERE st.topic=? AND s.dead=0 AND s.deleted=0 AND s.time<=? '
                    'ORDER BY s.time DESC,s.id DESC', (topic_id, overview['as_of']))]
                write_json(build / 'stories' / f'{topic_id}.json', posts)
                write_json(build / 'recent' / f'{topic_id}.json',
                           [p for p in posts if p['time'] >= overview['as_of'] - 30 * catalog.DAY])
                static_pages.write_topic(build, template, overview, detail, history, posts, version)
            search_export.write_summary(c, public, overview['as_of'])
        finish(build, output, template, overview, version)
    return output / 'index.html'

def finish(build, output, template, overview, version):
    public = build / 'public'
    (public / 'about').mkdir(exist_ok=True)
    (public / 'about' / 'index.html').write_text(static_pages.external_styles(static_pages.about_html(template), f'/releases/{version}/'), encoding='utf-8')
    search_export.write_page(public, SOURCE, f'/releases/{version}/')
    write_json(public / 'discovery-topics.json', {'topics': overview['topics']})
    trends_data = analytics.write(build, SOURCE, overview, version)
    shutil.copyfile(SOURCE / 'data.js', build / 'data.js')
    redirects = ['/search / 301', '/search/ / 301', '/search/index.html / 301']
    for item in overview['topics']:
        topic_id = str(item['id'])
        page = public / 'topic' / topic_id
        page.mkdir(parents=True, exist_ok=True)
        path = seo.topic_path(item)
        html = (public / 'topic' / item['slug'] / 'index.html').read_text(encoding='utf-8')
        # Numeric current-topic URLs redirect to their readable path.
        html = html.replace('</head>', f'<meta http-equiv="refresh" content="0;url={path}"></head>')
        (page / 'index.html').write_text(html, encoding='utf-8')
        redirects.extend([f'/topic/{topic_id} {path} 301', f'/topic/{topic_id}/ {path} 301'])
    (public / '_redirects').write_text('\n'.join(redirects) + '\n', encoding='utf-8')
    (public / 'sitemap.xml').write_text(seo.sitemap(overview), encoding='utf-8')
    (public / 'robots.txt').write_text(f'User-agent: *\nAllow: /\nSitemap: {seo.ORIGIN}/sitemap.xml\n', encoding='utf-8')
    (public / '404.html').write_text(static_pages.external_styles(static_pages.not_found_html(template), f'/releases/{version}/'), encoding='utf-8')
    html = static_html(template, overview, version, trends_data)
    (build / 'index.html').write_text(html, encoding='utf-8')
    # Keep previous releases so tabs opened before publication still work.
    release = output / 'releases' / version
    os.replace(build, release)
    shutil.copytree(release / 'public', output, dirs_exist_ok=True)
    os.replace(release / 'index.html', output / 'index.html')


def rebuild(source='dist', output='dist'):
    """Rebuild UI from an exported snapshot without ingestion or database queries."""
    source, output = Path(source), Path(output)
    match = re.search(r'name="hn-data" content="./releases/([0-9a-f]{32})/"', (source / 'index.html').read_text())
    if not match:
        raise ValueError('Source is not a static snapshot')
    old = source / 'releases' / match[1]
    overview = json.loads((old / 'topics.json').read_text())
    for item in overview['topics']:
        for removed in ('momentum', 'rising_score', 'is_rising'):
            item.pop(removed, None)
    version = uuid.uuid4().hex
    releases = output / 'releases'
    releases.mkdir(parents=True, exist_ok=True)
    template = (SOURCE / 'index.html').read_text()
    with tempfile.TemporaryDirectory(prefix='.build-', dir=releases) as temporary:
        build = Path(temporary)
        for directory in ('topics', 'stories', 'timelines'):
            shutil.copytree(old / directory, build / directory)
        write_json(build / 'topics.json', overview)
        (build / 'public').mkdir()
        (build / 'recent').mkdir()
        search_export.from_snapshot(old, build / 'public', overview['as_of'])
        static_pages.write_assets(build, SOURCE)
        for item in overview['topics']:
            ident = item['id']
            detail = json.loads((build / 'topics' / f'{ident}.json').read_text())
            posts = json.loads((build / 'stories' / f'{ident}.json').read_text())
            history = timeline.zoom_posts(posts, ident, overview['as_of'])
            timeline_file = build / 'timelines' / f'{ident}.json'
            timelines = json.loads(timeline_file.read_text())
            timelines['zoom'] = history
            timelines['0'] = timeline.history_posts(posts, ident, overview['as_of'])
            write_json(timeline_file, timelines)
            detail['trending'] = attention.select(posts, overview['as_of'])
            write_json(build / 'topics' / f'{ident}.json', detail)
            write_json(build / 'recent' / f'{ident}.json', [p for p in posts if p['time'] >= overview['as_of'] - 30 * catalog.DAY])
            static_pages.write_topic(build, template, overview, detail, history, posts, version)
        finish(build, output, template, overview, version)
    return output / 'index.html'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=str(DB))
    parser.add_argument('--output', default='dist')
    parser.add_argument('--from-snapshot', help='Rebuild UI from an existing static export; no database access')
    args = parser.parse_args()
    result = rebuild(args.from_snapshot, args.output) if args.from_snapshot else publish(args.db, args.output)
    print(f'Published {result}')
