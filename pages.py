"""Bounded extraction of public HTML pages linked from HN posts."""
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import certifi
import requests
import urllib3
from bs4 import BeautifulSoup

USER_AGENT = 'HN Atlas/1.0 (article metadata)'
BOT_WALLS = ('just a moment', 'are you a robot', 'enable js', 'checking your browser',
             'not a bot', 'abuse detection', 'welcome to reddit')
MAX_BYTES = 1_000_000


def public_url(url):
    """Return a parsed URL and a validated public IP to connect to."""
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Unsupported URL')
    if parsed.port and parsed.port not in (80, 443):
        raise ValueError('Unsupported port')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    public = [item[4][0] for item in addresses if ipaddress.ip_address(item[4][0]).is_global]
    if not addresses or len(public) != len(addresses):
        raise ValueError('Non-public destination')
    return parsed, public[0], port


def page(url, timeout=10):
    """Return status, media type and visible text, connecting only to a vetted IP."""
    for _ in range(4):
        parsed, ip, port = public_url(url)
        kwargs = {'host': ip, 'port': port}
        if parsed.scheme == 'https':
            kwargs.update(server_hostname=parsed.hostname, assert_hostname=parsed.hostname,
                          cert_reqs='CERT_REQUIRED', ca_certs=certifi.where())
            pool = urllib3.HTTPSConnectionPool(**kwargs)
        else:
            pool = urllib3.HTTPConnectionPool(**kwargs)
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query
        host_header = parsed.hostname if not parsed.port else parsed.netloc
        try:
            response = pool.urlopen('GET', path, headers={'Host': host_header, 'User-Agent': USER_AGENT},
                                    redirect=False, retries=False, preload_content=False,
                                    timeout=urllib3.Timeout(total=timeout))
            try:
                if 300 <= response.status < 400 and response.headers.get('Location'):
                    url = urljoin(url, response.headers['Location'])
                    continue
                ctype = response.headers.get('content-type', '').split(';')[0].lower()
                if ctype not in ('text/html', 'application/xhtml+xml'):
                    return response.status, ctype, ''
                body = bytearray()
                for chunk in response.stream(64_000):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        return response.status, ctype, ''
                soup = BeautifulSoup(body.decode('utf-8', errors='replace'), 'lxml')
                for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'form', 'noscript']):
                    tag.decompose()
                return response.status, ctype, ' '.join(soup.get_text(' ').split())
            finally:
                response.release_conn()
        except (OSError, urllib3.exceptions.HTTPError) as exc:
            raise requests.RequestException(str(exc)) from exc
        finally:
            pool.close()
    raise ValueError('Too many redirects')


def outcome(status, text):
    wall = any(marker in text[:400].lower() for marker in BOT_WALLS)
    return 'blocked' if status >= 400 or wall else 'ok' if len(text.split()) >= 300 else 'thin'
