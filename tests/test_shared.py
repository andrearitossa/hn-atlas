import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest
import attention
import embed
import routing
import timeline
from scripts.prepare_pages import validate_bundle


class SharedTests(unittest.TestCase):
    def test_article_identities_keep_their_persisted_shapes(self):
        post = dict(id=123, title='Story', url='https://www.EXAMPLE.com:8443/A/?b=hello%20world&a=2&utm_source=hn&a=1&fbclid=z#top')
        key = ('example.com:8443', '/A', 'a=1&a=2&b=hello+world')
        self.assertEqual(attention.article_key(post), key)
        self.assertEqual(hashlib.sha256(json.dumps(attention.article_key(post), separators=(',', ':')).encode()).hexdigest(),
                         'ab556efb8412092e26d9872dca5c831847f0e76a929ae0fa4de198eef038c295')
        self.assertEqual(timeline.article_key(post), '//example.com:8443/A?b=hello%20world&a=2&utm_source=hn&a=1&fbclid=z')
        self.assertEqual(routing.metadata(post['title'], post['url']),
                         ('https://www.example.com:8443/A/?b=hello+world&a=2&a=1', 'link', 'example.com'))
        for url in [None, 'javascript:alert(1)', 'https://[']:
            self.assertEqual(attention.article_key(dict(id=123, url=url)), 123)
            self.assertEqual(timeline.article_key(dict(id=123, url=url)), '123')

    def test_embedding_constants_match_across_runtimes(self):
        source = (Path(__file__).resolve().parents[1] / 'lib/search-embedding.js').read_text()
        self.assertEqual(re.search(r"EMBEDDING_MODEL = '([^']+)'", source)[1], embed.MODEL)
        self.assertEqual(int(re.search(r'EMBEDDING_DIMENSIONS = (\d+)', source)[1]), embed.DIM)

    def test_bundle_checks_module_imports(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'index.html').write_text('<script type="module" src="/search.js"></script>')
            (root / 'search.js').write_text("import {escapeHTML} from './lib/escape.js';")
            with self.assertRaisesRegex(ValueError, 'Missing module asset'):
                validate_bundle(root)
            (root / 'lib').mkdir()
            (root / 'lib/escape.js').write_text('export const escapeHTML = x => x;')
            validate_bundle(root)
