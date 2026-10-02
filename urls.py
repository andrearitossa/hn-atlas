"""URL parsing pieces; callers retain their existing identity conventions."""
from urllib.parse import parse_qsl, urlsplit


def split(value):
    try:
        return urlsplit(value or '')
    except ValueError:
        return urlsplit('')


def content_query(query):
    return [(k, v) for k, v in parse_qsl(query, keep_blank_values=True)
            if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')]
