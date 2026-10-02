"""Weekly topic volumes derived from the same public snapshot as topic pages."""
from collections import Counter
from datetime import datetime, timezone
import json
import re

WEEK = 7 * 86400
MONDAY = 4 * 86400  # 1970-01-05 UTC


def week_start(timestamp):
    return (int(timestamp) - MONDAY) // WEEK * WEEK + MONDAY


def period_start(timestamp, timeframe):
    if timeframe == 'week':
        return week_start(timestamp)
    date = datetime.fromtimestamp(timestamp, timezone.utc)
    return int(datetime(date.year, date.month if timeframe == 'month' else 1, 1,
                        tzinfo=timezone.utc).timestamp())


def period_end(timestamp, timeframe):
    if timeframe == 'week':
        return timestamp + WEEK
    date = datetime.fromtimestamp(timestamp, timezone.utc)
    year, month = (date.year + 1, 1) if timeframe == 'year' or date.month == 12 else (date.year, date.month + 1)
    return int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp())


def build(overview, read_posts):
    topics = sorted(overview['topics'], key=lambda t: (t['name'].casefold(), t['id']))
    counters = {key: [] for key in ('week', 'month', 'year')}
    selections = {key: [] for key in counters}
    earliest = overview['as_of']
    seen_posts = set()
    monthly = Counter()
    for topic in topics:
        counts = {key: Counter() for key in counters}
        best = {key: {} for key in counters}
        for post in read_posts(topic['id']):
            if post['time'] > overview['as_of'] or post.get('dead') or post.get('deleted'):
                continue
            earliest = min(earliest, post['time'])
            if post['id'] not in seen_posts:
                seen_posts.add(post['id'])
                monthly[datetime.fromtimestamp(post['time'], timezone.utc).strftime('%Y-%m')] += 1
            rank = (post.get('score') or 0, post['time'], post['id']) if post.get('id') is not None else None
            for key in counts:
                start = period_start(post['time'], key)
                counts[key][start] += 1
                if rank is not None:
                    previous = best[key].get(start)
                    if previous is None or rank > (previous.get('score') or 0, previous['time'], previous['id']):
                        best[key][start] = post
        for key in counters:
            counters[key].append(counts[key])
            selections[key].append(best[key])
    periods = {}
    stories = {}
    for key, counts in counters.items():
        starts, ends = [], []
        start = period_start(earliest, key)
        while start <= overview['as_of']:
            starts.append(start)
            start = period_end(start, key)
            ends.append(start)
        picks = []
        for start in starts:
            row = []
            for chosen in selections[key]:
                post = chosen.get(start)
                row.append(post['id'] if post else None)
                if post:
                    stories[str(post['id'])] = {field: post.get(field) for field in ('id', 'title', 'score', 'time')}
            picks.append(row)
        periods[key] = {'starts': starts, 'ends': ends,
                        'counts': [[c[start] for c in counts] for start in starts],
                        'picks': picks}
    return {'monthly': [{'month': datetime.fromtimestamp(start, timezone.utc).strftime('%Y-%m'), 'posts': monthly[datetime.fromtimestamp(start, timezone.utc).strftime('%Y-%m')]} for start in periods['month']['starts']],
            'as_of': overview['as_of'], 'periods': periods,
            'stories': stories,
            'topics': [{**{field: t[field] for field in ('id', 'name', 'slug')},
                        **({'description': t['description']} if 'description' in t else {})}
                       for t in topics]}


def render(data, source, overview=None, version=None):
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    html = (source / 'analytics.html').read_text()
    html = html.replace('<link rel="stylesheet" href="__RELEASE__analytics.css">',
                        '<style>' + (source / 'analytics.css').read_text() + '</style>')
    html = html.replace('<script defer src="__RELEASE__analytics.js"></script>', '')
    html = html.replace('</body>', '<script id="analytics-data" type="application/json">' + payload +
                        '</script><script>' + (source / 'analytics.js').read_text() + '</script></body>')
    import static_pages
    html = html.replace('<!-- GLOBAL_ACTIVITY_CHART -->', static_pages.chart_html(data.get('monthly', [])))
    html = html.replace('</body>', '<script>' + (source / 'activity.js').read_text() + '</script></body>')
    graph_data = json.dumps(overview, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c') if overview else None
    if version:
        html = html.replace('id="topic-graph"', f'id="topic-graph" data-root="/releases/{version}/"')
    graph = ('<script id="topic-graph-data" type="application/json">' + graph_data + '</script>') if graph_data else ''
    graph += '<script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script><script>' + (source / 'topic_graph.js').read_text() + '</script>'
    html = html.replace('</body>', graph + '</body>')
    return html


def write(release, source, overview, version):
    data = build(overview, lambda ident: json.loads((release / 'stories' / f'{ident}.json').read_text()))
    page = release / 'public' / 'analytics'
    page.mkdir(parents=True, exist_ok=True)
    (page / 'index.html').write_text(render(data, source, overview, version))
    return data


def embedded(data, source):
    """Self-contained section, with isolated IDs/styles for the central page."""
    template = (source / 'analytics.html').read_text()
    content = re.search(r'<main[^>]*>(.*?)</main>', template, re.S)[1]
    content = re.sub(r'<section id="global-activity".*?</section>', '', content, flags=re.S)
    content = re.sub(r'<section id="topic-graph".*?</section>', '', content, flags=re.S)
    content = content.replace('<h1>', '<h2>').replace('</h1>', '</h2>')
    ids = re.findall(r'\bid="([^"]+)"', content) + ['analytics-data']
    def attributes(match):
        return match[1] + '="' + ' '.join('tr-' + value if value in ids else value
                                        for value in match[2].split()) + '"'
    content = re.sub(r'\b(id|for|aria-labelledby|aria-describedby|aria-controls)="([^"]+)"', attributes, content)
    css = re.sub(r'/\*.*?\*/', '', (source / 'analytics.css').read_text(), flags=re.S)
    for ident in sorted(ids, key=len, reverse=True):
        css = re.sub(r'#' + re.escape(ident) + r'(?![\w-])', '#tr-' + ident, css)
    def scope(match):
        selectors, declarations = match.groups()
        if selectors.strip().startswith('@') or re.fullmatch(r'\s*(?:from|to|[\d.]+%)\s*', selectors):
            return match[0]
        scoped = []
        for selector in selectors.split(','):
            selector = selector.strip()
            if selector in (':root', 'body', 'main', 'main.trends-view', '.trends-view'):
                scoped.append('.trends-view')
            elif selector.startswith('.trends-view'):
                scoped.append(selector)
            else:
                scoped.append('.trends-view ' + selector)
        return ','.join(scoped) + '{' + declarations + '}'
    css = re.sub(r'([^{}]+)\{([^{}]*)\}', scope, css)
    css += '.connections-page .trends-view{padding:0;max-width:none;margin:0;background:transparent}.trends-view .page-heading h2{font-size:clamp(26px,3vw,36px);line-height:1.1;letter-spacing:-.04em;margin:0}'
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    script = (source / 'analytics.js').read_text()
    return ('<div class="trends-view"><style>' + css + '</style>' + content +
            '<script id="tr-analytics-data" type="application/json">' + payload + '</script>' +
            '<script data-prefix="tr-">' + script + '</script></div>')
