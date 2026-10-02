"""Complete topic HTML; the browser enhances it without fetching startup data."""
from datetime import datetime, timezone
from html import escape
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import seo


def stamp(value, fmt='%b %d, %Y'):
    return datetime.fromtimestamp(value, timezone.utc).strftime(fmt)


def safe_url(value):
    try:
        url = urlsplit(value or '')
        return value if url.scheme in ('http', 'https') and url.netloc else ''
    except ValueError:
        return ''


def post(p):
    hn = f'https://news.ycombinator.com/item?id={p["id"]}'
    url = safe_url(p.get('url')) or hn
    domain = urlsplit(url).netloc.removeprefix('www.')
    return (f'<div class="post"><a href="{escape(url, quote=True)}" target="_blank" rel="noopener">{escape(p["title"] or "Untitled")}</a>'
            f'<small><a href="{escape(url, quote=True)}" target="_blank" rel="noopener">↗ {escape(domain)}</a> · '
            f'<a href="{hn}">HN · {p.get("descendants") or 0} comments</a> · {p["score"]} points · {stamp(p["time"])}</small></div>')


def periods_html(periods, limit=5, level='year'):
    sections = []
    for period in reversed(periods):
        year = stamp(period['start'], '%Y')
        label = stamp(period['start'], {'year': '%Y', 'month': '%b %Y', 'week': '%b %d, %Y'}[level])
        picks = sorted(period['posts'][:limit], key=lambda p: (p['time'], p['id']), reverse=True)
        sections.append(f'<section class="zt-period" data-year="{year}" data-time="{period["start"]}"><div class="zt-date"><span class="zt-dot"></span>'
                        f'<strong>{label}</strong><small>{period["count"]:,} stories</small></div><div class="zt-stories">' +
                        (''.join(post(p) for p in picks) or '<p class="meta">A quiet stretch.</p>') + '</div></section>')
    return ''.join(sections)


def chart_html(monthly):
    maximum = max((m['posts'] for m in monthly), default=1) or 1
    bars = []
    width = 900 / max(1, len(monthly))
    for i, m in enumerate(monthly):
        height = 145 * m['posts'] / maximum
        label = f'{m["month"]}: {m["posts"]:,} posts'
        bars.append(f'<rect x="{50+i*width:.2f}" y="{170-height:.2f}" width="{max(.5,width*.8):.2f}" height="{height:.2f}" rx="2" '
                    f'tabindex="0" role="button" data-month="{m["month"]}" aria-label="{label}"><title>{label}</title></rect>')
    labels = '' if not monthly else f'<span>{monthly[0]["month"]}</span><span>{monthly[-1]["month"]}</span>'
    return '<svg id="activity-chart" viewBox="40 15 920 160" preserveAspectRatio="none" role="group" aria-label="Monthly posts">' + ''.join(bars) + '</svg><div class="chart-axis" id="chart-axis">' + labels + '</div>'


def render(template, overview, detail, history, posts, version):
    ident = detail['id']
    me = next(t for t in overview['topics'] if t['id'] == ident)
    recent = [p for p in posts if p['time'] >= overview['as_of'] - 30 * 86400]
    ranked = sorted(recent, key=lambda p: (p['score'] is not None, p['score'] or 0, p['id']), reverse=True)
    years = sorted({m['month'][:4] for m in detail['monthly'] if m['month']}, reverse=True)
    year_options = ''.join(f'<option value="{y}">{y}</option>' for y in years)
    related_ids = {e['b'] if e['a'] == ident else e['a'] for e in overview['edges'] if ident in (e['a'], e['b'])}
    related = [t for t in overview['topics'] if t['id'] in related_ids][:4]
    cards = ''.join(f'<a href="{seo.topic_path(t)}"><strong>{escape(t["name"])} ↗</strong><p>{escape(t["description"] or "")}</p></a>' for t in related)
    body = f'''<div class="page" data-topic="{ident}">
<a href="/#/topics" class="meta">← All topics</a>
<div class="hero topic-hero"><div><h1>{escape(detail['name'])}</h1><p class="topic-description">{escape(detail['description'] or '')}</p>
<p class="topic-stats">{detail['size']:,} stories <span>·</span> {me['last_7d']} in 7 days <span>·</span> Updated {stamp(detail['as_of'])}</p>
</div></div>
<nav class="topic-nav" aria-label="On this topic page"><a href="#topic-recent">01 <span>Now</span></a><a href="#topic-history">02 <span>Timeline</span></a><a href="#topic-activity">03 <span>Activity</span></a><a href="#topic-archive">04 <span>Explore</span></a><a class="topic-follow-link" href="#topic-updates">Weekly selection ↗</a></nav>
<section id="topic-recent" class="recent-stories" tabindex="-1"><p class="eyebrow">01 / CATCH UP</p><h2>The conversation now</h2><p class="section-intro">Popular recent stories, shown newest first.</p>
{''.join(post(p) for p in sorted(detail['trending'][:5], key=lambda p: (p['time'], p['id']), reverse=True)) or '<p class="meta">No recent stories.</p>'}</section>
<details class="topic-follow" id="topic-updates"><summary><span><strong>Keep up with this topic</strong><small>A few highlights, once a week.</small></span><span class="topic-follow-action">Get the weekly email <span aria-hidden="true">＋</span></span></summary>
<div class="topic-follow-panel"><p>Weekly highlights from {escape(detail['name'])}, Sundays at 18:00 Stockholm time.</p><form id="follow-form"><input name="email" type="email" placeholder="you@example.com" required aria-label="Email address"><label class="contact-check" aria-hidden="true">Website<input name="website" tabindex="-1" autocomplete="off"></label><button>Send me weekly highlights</button></form>
<small>One email a week about this topic. Unsubscribe any time.</small><small id="follow-status" role="status"></small></div></details>
<section id="topic-history" class="topic-history" tabindex="-1"><div class="history-heading"><div><p class="eyebrow">02 / EXPLORE THROUGH TIME</p><h2>Timeline</h2></div></div>
<p class="section-intro">Start with the latest. Scroll back through time, or zoom in for more.</p>
<div class="zt-shell"><div class="zt-toolbar"><div class="zt-scale"><strong id="zt-scale-name">Year by year</strong><span id="zt-scale-hint">Up to 5 stories per year</span></div>
<div class="zt-zoom"><button id="zt-out" aria-label="Zoom out">−</button><input id="zt-zoom" type="range" min="0" max="3" value="1" aria-label="Timeline detail"><button id="zt-in" aria-label="Zoom in">+</button></div>
<label class="zt-jump"><span>Jump to</span><select id="zt-year" aria-label="Jump to year">{year_options}</select></label></div>
<div class="zt-view" id="zt-view" tabindex="0" role="region" aria-label="Scrollable news timeline"><div id="zt-periods">{periods_html(history['levels']['year'])}</div></div>
<template id="zt-month">{periods_html(history['levels']['month'], 3, 'month')}</template>
<template id="zt-week">{periods_html(history['levels']['week'], 3, 'week')}</template>
<div class="zt-footer"><span>Scroll through time ↕</span><button id="zt-reset">Reset view</button></div></div>
<p id="timeline-status" class="meta" role="status"></p></section>
<section id="topic-activity" class="activity" tabindex="-1"><div class="activity-content"><div class="chart-heading"><div><h2>The pace of conversation</h2><p id="chart-readout" aria-live="polite">Posts per month</p></div>
<div class="sort" aria-label="Activity period"><button data-range="12">1 year</button><button data-range="24">2 years</button><button data-range="0" class="on">All</button></div></div>
{chart_html(detail['monthly'])}<p class="chart-note">Posts per month · first and latest months may be partial.</p></div></section>
<section id="topic-archive" class="topic-archive" tabindex="-1"><p class="eyebrow">04 / GO DEEPER</p><h2>Follow your curiosity</h2><p class="section-intro">Find a particular story, revisit a year, or see what resonated.</p>
<form id="story-search" class="browse-tools"><input name="q" type="search" maxlength="200" placeholder="Search titles or websites…" aria-label="Search stories">
<select name="sort" aria-label="Sort stories"><option value="newest">Newest</option><option value="top" selected>Highest rated</option><option value="discussed">Most discussed</option></select>
<select name="days" aria-label="Story period"><option value="0">All time</option><option value="7">Last week</option><option value="30" selected>Last month</option><option value="365">Last year</option></select>
<select name="year" aria-label="Archive year"><option value="0">Any year</option>{year_options}</select><button>Search</button><button type="button" id="reset-search" hidden>Clear filters</button></form>
<p id="archive-context" class="meta">Highest rated · the last 30 days</p><div id="story-results">{''.join(post(p) for p in ranked[:10])}</div>
<p id="story-status" class="meta" role="status">{min(10,len(ranked))} stories shown.</p><button id="more-stories" {'hidden' if len(ranked)<=10 else ''}>Load more stories</button></section>
<footer class="topic-footer"><div><p class="eyebrow">KEEP EXPLORING</p><h2>A little further afield</h2><div class="related-cards">{cards}</div></div>
</footer></div>'''
    footer = re.search(r'<footer class="site-footer" hidden>.*?</footer>', template, re.S)[0].replace(' hidden', '')
    body = body[:-6] + footer + '</div>'
    html = seo.render(template, topic=detail)
    html = html.replace('<a id="topics" href="/#/topics">', '<a id="topics" class="on" aria-current="page" href="/#/topics">', 1)
    html = re.sub(r'<footer class="site-footer" hidden>.*?</footer>', '', html, count=1, flags=re.S)
    html = re.sub(r'<main id="app">.*?</main>', lambda _: f'<main id="app">{body}</main>', html, count=1, flags=re.S)
    html = html[:html.index('<script src=')] + '</body></html>'
    root = f'../../releases/{version}/'
    html = html.replace('name="hn-data" content=""', f'name="hn-data" content="{root}"')
    initial = {'id': ident, 'name': detail['name'], 'as_of': detail['as_of'], 'monthly': detail['monthly'], 'years': years}
    payload = json.dumps(initial, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    html = html.replace('</body>', f'<script id="topic-initial" type="application/json">{payload}</script><script defer src="{root}topic.js"></script></body>')
    html = html.replace('<footer class="site-footer" hidden>', '<footer class="site-footer">')
    return html


def write_assets(release, source):
    """One cached stylesheet and one small topic enhancement script per release."""
    template = (source / 'index.html').read_text()
    css = re.search(r'<style>(.*?)</style>', template, re.S)[1]
    css += '\n.chart-axis { display:flex; justify-content:space-between; color:var(--muted); font-size:11px; margin:8px 1% 12px; }\n.page #activity-chart { height:180px; margin-top:20px; }\n@media(max-width:600px) { .page #activity-chart { height:120px; } }\n.topic-nav a { color:var(--hn); font-size:10px; padding:6px 0; }\n.zt-stories .post { margin:0 0 18px; }\n'
    (release / 'site.css').write_text(css)
    (release / 'topic.js').write_text((source / 'topic.js').read_text())
    for asset in ('search.js', 'search.css'):
        (release / asset).write_text((source / asset).read_text())


def external_styles(html, root):
    html = re.sub(r'<style>.*?</style>', lambda _: f'<link rel="stylesheet" href="{root}site.css">', html, count=1, flags=re.S)
    for asset in ('search.js', 'search.css'):
        html = html.replace('/' + asset, root + asset)
    return html


def write_topic(release, template, overview, detail, history, posts, version):
    """Build the HTML plus optional, independently downloadable archive/history slices."""
    ident = detail['id']
    archive = release / 'archive' / str(ident)
    archive.mkdir(parents=True, exist_ok=True)
    by_year = {}
    for p in posts:
        by_year.setdefault(stamp(p['time'], '%Y'), []).append(p)
    for year, items in by_year.items():
        (archive / f'{year}.json').write_text(json.dumps(items, ensure_ascii=False, separators=(',', ':')))
    (release / 'history').mkdir(exist_ok=True)
    for level in ('month', 'week'):
        rows = [{**period, 'posts': period['posts'][:3]} for period in history['levels'][level]]
        (release / 'history' / f'{ident}-{level}.json').write_text(json.dumps(rows, ensure_ascii=False, separators=(',', ':')))
    html = external_styles(render(template, overview, detail, history, posts, version), f'../../releases/{version}/')
    page = release / 'public' / 'topic' / detail['slug']
    page.mkdir(parents=True, exist_ok=True)
    (page / 'index.html').write_text(html)
    return html


def home_content(template, overview):
    # Reuse the exact map shell from the live UI; only the SVG needs enhancement.
    shell = template.split('$("#app").innerHTML=`', 1)[1].split('`;', 1)[0]
    cards = []
    for t in sorted(overview['topics'], key=lambda t: -t['last_7d']):
        cards.append(f'<a class="topic-card" href="{seo.topic_path(t)}"><strong>{escape(t["name"])}</strong><p>{escape(t["description"] or "")}</p>'
                     f'<div class="card-kicker"><span>{t["last_7d"]:,} posts · past 7 days</span></div></a>')
    ui = re.search(r'const shell=`(.*?)`;', (Path(__file__).parent / 'search.js').read_text(), re.S)[1]
    ui = ui.replace('<div id="directory" class="directory-grid"></div>', '<div id="directory" class="directory-grid">' + ''.join(cards) + '</div>')
    ui = ui.replace('<p id="result-count" role="status"></p>', f'<p id="result-count" role="status">{len(cards)} topics</p>')
    directory = '<div class="home"><div class="home-inner discovery">' + ui + '</div></div>'
    return shell.replace('<section id="browse-section" aria-label="Browse topics"></section>',
                         f'<section id="browse-section" aria-label="Browse topics" data-prerendered="true">{directory}</section>')
