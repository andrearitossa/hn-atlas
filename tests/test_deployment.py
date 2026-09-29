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
