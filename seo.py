"""Public HTML shared by static exports and the optional live server."""
from html import escape
import re

ORIGIN = 'https://hackeratlas.com'
DESCRIPTION = 'Explore Hacker News by topic: discover stories, follow trends, and browse the archive.'


def topic_path(topic):
    return f'/topic/{topic["slug"]}/'


def render(template, topic=None, overview=None):
    title = f"{topic['name']} · HN Atlas" if topic else 'HN Atlas'
    description = (topic.get('description') or DESCRIPTION) if topic else DESCRIPTION
    url = ORIGIN + (topic_path(topic) if topic else '/')
    metadata = '\n'.join([
        f'<title>{escape(title)}</title>',
        f'<meta name="description" content="{escape(description, quote=True)}">',
        f'<link rel="canonical" href="{url}">',
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="Hacker Atlas">',
        *[f'<meta property="og:{key}" content="{escape(value, quote=True)}">'
          for key, value in [('title', title), ('description', description), ('url', url)]],
        '<meta name="twitter:card" content="summary">',
        f'<meta name="twitter:title" content="{escape(title, quote=True)}">',
        f'<meta name="twitter:description" content="{escape(description, quote=True)}">',
    ])
    html = re.sub(r'<title>.*?</title>', lambda _: metadata, template, count=1)
    if topic:
        stories = ''.join(f'<li><a href="https://news.ycombinator.com/item?id={int(p["id"])}">{escape(p["title"] or "Untitled")}</a></li>' for p in topic.get('top', []))
        content = f'<div class="page"><a href="/#/topics">All topics</a><h1>{escape(topic["name"])}</h1><p>{escape(description)}</p><h2>Popular stories</h2><ul>{stories}</ul></div>'
    else:
        links = ''.join(f'<li><a href="{topic_path(t)}">{escape(t["name"])}</a></li>' for t in (overview or {}).get('topics', []))
        content = f'<div class="page"><h1>Hacker Atlas</h1><p>{DESCRIPTION}</p><ul>{links}</ul></div>'
    return html.replace('<main id="app"></main>', f'<main id="app">{content}</main>')


def sitemap(overview):
    urls = [ORIGIN + '/', *[ORIGIN + topic_path(t) for t in overview['topics']]]
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join(f'<url><loc>{url}</loc></url>' for url in urls) + '</urlset>'
