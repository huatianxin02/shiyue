"""Public artifacts must not contain library data, credentials or external symlinks."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('public_release',Path(__file__).resolve().parents[1]/'tools/public_release.py')
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)


class PublicReleaseTests(unittest.TestCase):
    def test_private_files_and_paths_block_release(self):
        self.assertTrue(release.privacy_issues('runtime.json',b'{}'))
        self.assertTrue(release.privacy_issues('keys/owner.jks',b'fake'))
        self.assertTrue(release.privacy_issues('docs/report.md',('/Users/'+'private-owner/document').encode()))
        self.assertFalse(release.privacy_issues('docs/example.md',b'/Users/example/document'))
    def test_credentials_report_only_reason(self):
        secret=('ghp_'+'x'*36).encode()
        issues=release.privacy_issues('config.txt',secret)
        self.assertIn('credential pattern',issues)
        self.assertNotIn(secret.decode(),str(issues))
        self.assertFalse(release.privacy_issues('licenses.py', b'Asterisk-linking-protocols-exception'))
    def test_external_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);root=base/'release';root.mkdir();outside=base/'private.txt';outside.write_text('private')
            link=root/'innocent.txt';link.symlink_to(outside)
            with self.assertRaisesRegex(ValueError,'symlink'):release.scan([link],root)
    def test_existing_release_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source=root/'source';source.mkdir();(source/'README.md').write_text('safe')
            archive=root/'release.zip';archive.write_bytes(b'previous release')
            with self.assertRaises(FileExistsError):release.archive_tree(source,archive)
            self.assertEqual(archive.read_bytes(),b'previous release')
    def test_archive_bytes_are_reproducible(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source=root/'source';source.mkdir();(source/'README.md').write_text('safe')
            release.archive_tree(source,root/'first.zip');release.archive_tree(source,root/'second.zip')
            self.assertEqual((root/'first.zip').read_bytes(),(root/'second.zip').read_bytes())
    def test_private_development_directories_are_not_selected(self):
        names={str(p.relative_to(release.ROOT)) for p in release.source_files()}
        self.assertFalse(any(n.startswith(('qa/','backups/','data/','delivery-','node_modules/')) for n in names))
        self.assertNotIn('agent-kit/验证结果.json',names)


if __name__=='__main__':unittest.main()
