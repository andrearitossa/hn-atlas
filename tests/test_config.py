import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from config import load_jsonc, admin_email


class ConfigTests(unittest.TestCase):
    def test_jsonc_preserves_strings_and_strips_comments_and_trailing_commas(self):
        source = r'''{
          // configuration
          "url": "https://example.com/a//b/*c*/,}",
          "escaped": "quote\" // retained",
          /* block
             comment */ "items": [1, 2,], "object": {"a": 1,},
        }'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.jsonc'
            path.write_text(source)
            self.assertEqual(load_jsonc(path), dict(url='https://example.com/a//b/*c*/,}',
                escaped='quote" // retained', items=[1, 2], object={'a': 1}))

    def test_admin_email_is_required(self):
        with patch.dict(os.environ, {'ADMIN_EMAIL': ''}):
            with self.assertRaisesRegex(RuntimeError, 'ADMIN_EMAIL'):
                admin_email()
        with patch.dict(os.environ, {'ADMIN_EMAIL': ' admin@example.com '}):
            self.assertEqual(admin_email(), 'admin@example.com')
