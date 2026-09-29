"""A concurrent local build or a misleading Wrangler exit cannot mark success."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for directory in ('scripts', '.venv/bin', 'node_modules/.bin', 'pages-dist'):
            (self.root / directory).mkdir(parents=True)
        shutil.copyfile(Path(__file__).resolve().parents[1] / 'scripts/deploy_site.sh',
                        self.root / 'scripts/deploy_site.sh')
        self.executable('.venv/bin/python', '''import sys
from pathlib import Path
output=Path(sys.argv[sys.argv.index('--output')+1])
output.mkdir(parents=True)
(output/'index.html').write_text('Complete bundle')
''')
        # Bash functions take precedence over the deployment script's Node PATH.
        self.npm_stub = self.root / 'npm-stub.sh'
        self.npm_stub.write_text('''npm() {
  if [[ "$*" == "run test:usability" ]]; then
    [[ -f "$SITE_BUNDLE/index.html" ]] || return 99
    printf '%s' "$SITE_BUNDLE" > checked-bundle
  fi
  return 0
}
''')

    def executable(self, name, code):
        path = self.root / name
        path.write_text('#!/usr/bin/env python3\n' + code)
        path.chmod(0o755)

    def run_deploy(self):
        return subprocess.run(['bash', 'scripts/deploy_site.sh'], cwd=self.root,
                              capture_output=True, text=True,
                              env={**os.environ, 'BASH_ENV': str(self.npm_stub)})

    def test_failed_checks_prevent_upload(self):
        for failing in ('test', 'run test:usability'):
            with self.subTest(failing=failing):
                self.npm_stub.write_text(f'npm() {{ [[ "$*" != "{failing}" ]]; }}\n')
                self.executable('node_modules/.bin/wrangler',
                                "from pathlib import Path\nPath('uploaded').touch()\n")
                result = self.run_deploy()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / 'uploaded').exists())
                self.assertFalse(list((self.root / '.wrangler').glob('pages-deploy.*')))

    def test_zero_exit_upload_error_is_not_success(self):
        self.executable('node_modules/.bin/wrangler', "print('ERROR Failed to upload files: ENOENT')\n")
        result = self.run_deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('publication failed', result.stderr)

    def test_replacing_shared_bundle_does_not_affect_the_upload(self):
        self.executable('node_modules/.bin/wrangler', '''import sys, shutil
from pathlib import Path
bundle=Path(sys.argv[3])
assert Path('checked-bundle').read_text()==str(bundle)
shutil.rmtree('pages-dist')
Path('pages-dist').mkdir()
assert (bundle/'index.html').read_text()=='Complete bundle'
print('Deployment complete! https://example.pages.dev')
''')
        result = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(list((self.root / '.wrangler').glob('pages-deploy.*')))


class DailyLocalCopyTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for directory in ('scripts', '.venv/bin', 'data', 'dist/releases/old'):
            (self.root / directory).mkdir(parents=True)
        shutil.copyfile(Path(__file__).resolve().parents[1] / 'scripts/update_site.sh',
                        self.root / 'scripts/update_site.sh')
        (self.root / 'dist/index.html').write_text('Old index')
        (self.root / 'dist/releases/old/data.json').write_text('Old data')
        python = self.root / '.venv/bin/python'
        python.write_text('''#!/usr/bin/env python3
import sys
from pathlib import Path
output = Path(sys.argv[sys.argv.index('--publish') + 1])
(output / 'releases/new').mkdir(parents=True)
(output / 'releases/new/data.json').write_text('New data')
(output / 'index.html').write_text('New index')
(output / 'sitemap.xml').write_text('New sitemap')
''')
        python.chmod(0o755)

    def run_daily(self, fail=False):
        (self.root / 'scripts/deploy_site.sh').write_text('''#!/bin/bash
set -euo pipefail
[[ $(cat dist/index.html) == 'Old index' ]]
cp "$1/index.html" deployed-index
cp "$1/releases/new/data.json" deployed-data
''' + ('exit 1\n' if fail else 'exit 0\n'))
        return subprocess.run(['bash', 'scripts/update_site.sh', '--force'],
                              cwd=self.root, capture_output=True, text=True)

    def test_success_retains_exact_deployed_export_and_previous_release(self):
        result = self.run_daily()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for local, deployed in [('dist/index.html', 'deployed-index'),
                                ('dist/releases/new/data.json', 'deployed-data')]:
            self.assertEqual((self.root / local).read_bytes(),
                             (self.root / deployed).read_bytes())
        self.assertEqual((self.root / 'dist/sitemap.xml').read_text(), 'New sitemap')
        self.assertEqual((self.root / 'dist/releases/old/data.json').read_text(), 'Old data')
        self.assertTrue((self.root / 'data/update-site-success.date').exists())
        self.assertFalse(list((self.root / '.wrangler').glob('daily-build.*')))

    def test_failed_deployment_preserves_local_copy_and_does_not_mark_success(self):
        result = self.run_daily(fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / 'dist/index.html').read_text(), 'Old index')
        self.assertFalse((self.root / 'dist/releases/new').exists())
        self.assertFalse((self.root / 'data/update-site-success.date').exists())
