"""Copy the current static release into a small Cloudflare Pages upload folder."""
import argparse
import json
from pathlib import Path
import re
import shutil
import tempfile


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
        shutil.copytree(source / release, staged / release)
        html = html.replace('<meta name="hn-newsletter" content="">',
                            '<meta name="hn-newsletter" content="cloudflare">')
        html = html.replace('<meta name="hn-feedback" content="">',
                            '<meta name="hn-feedback" content="cloudflare">')
        (staged / 'index.html').write_text(html, encoding='utf-8')
        overview = json.loads((source / release / 'topics.json').read_text())
        (staged / 'newsletter-topics.json').write_text(json.dumps(
            {str(topic['id']): topic['name'] for topic in overview['topics']}))
        (staged / '_routes.json').write_text(json.dumps({
            'version': 1, 'include': ['/api/newsletter/*', '/api/feedback'], 'exclude': []}))
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
