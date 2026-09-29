"""Copy the current static release into a small Cloudflare Pages upload folder."""
import argparse
import json
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit


def validate_bundle(root):
    """Reject broken local script/style references before replacing the upload folder."""
    root = root.resolve()
    for page in [root / 'index.html', *(root / 'topic').glob('*/index.html'), *(root / 'analytics').glob('index.html')]:
        html = page.read_text()
        refs = re.findall(r'<script[^>]+src="([^" ]+)"', html)
        refs += re.findall(r'<link rel="stylesheet" href="([^" ]+)"', html)
        for ref in refs:
            url = urlsplit(ref)
            if url.scheme or url.netloc:
                continue
            target = (root / url.path.lstrip('/') if url.path.startswith('/') else page.parent / url.path).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                raise ValueError(f'Missing public asset: {page.relative_to(root)} -> {ref}')
    for file in root.rglob('*'):
        if file.is_file() and file.stat().st_size > 25 * 1024 * 1024:
            raise ValueError(f'Public file exceeds the Pages limit: {file.relative_to(root)}')


def prepare(source='dist', output='pages-dist'):
    source, output = Path(source), Path(output)
    html = (source / 'index.html').read_text(encoding='utf-8')
    match = re.search(r'name="hn-data" content="\./releases/([0-9a-f]{32})/"', html)
    if not match:
        raise ValueError('The static index does not reference a published release')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.pages-', dir=output.parent) as temporary:
        staged = Path(temporary) / 'site'
        release = Path('releases') / match[1]
        # These are build inputs; topic pages use year shards and on-demand history.
        ignored = shutil.ignore_patterns('public', 'stories', 'timelines') if (source / release / 'topic.js').exists() else shutil.ignore_patterns('public')
        shutil.copytree(source / release, staged / release, ignore=ignored)
        shutil.copytree(source / release / 'public', staged, dirs_exist_ok=True)
        html = html.replace('<meta name="hn-newsletter" content="">',
                            '<meta name="hn-newsletter" content="cloudflare">')
        html = html.replace('<meta name="hn-feedback" content="">',
                            '<meta name="hn-feedback" content="cloudflare">')
        (staged / 'index.html').write_text(html, encoding='utf-8')
        for page in (staged / 'topic').glob('*/index.html'):
            topic_html = page.read_text(encoding='utf-8')
            for feature in ('newsletter', 'feedback'):
                topic_html = topic_html.replace(f'name="hn-{feature}" content=""', f'name="hn-{feature}" content="cloudflare"')
            page.write_text(topic_html, encoding='utf-8')
        overview = json.loads((source / release / 'topics.json').read_text())
        (staged / 'newsletter-topics.json').write_text(json.dumps(
            {str(topic['id']): topic['name'] for topic in overview['topics']}))
        (staged / '_routes.json').write_text(json.dumps({
            'version': 1, 'include': ['/api/newsletter/*', '/api/feedback'], 'exclude': []}))
        (staged / '_headers').write_text('/releases/*\n  Cache-Control: public, max-age=31536000, immutable\n')
        validate_bundle(staged)
        if output.exists():
            shutil.rmtree(output)
        staged.rename(output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='dist')
    parser.add_argument('--output', default='pages-dist')
    args = parser.parse_args()
    print(f'Prepared {prepare(args.source, args.output)}')
