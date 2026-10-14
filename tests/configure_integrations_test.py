import json
import os
from pathlib import Path
import tempfile
import unittest

from backend.configure_integrations import ConfigurationError, import_settings, parse


class PrivateConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.target = Path(self.directory.name) / 'backend.env'
        self.source = Path(self.directory.name) / 'provider.env'
        self.original = ('# private runtime\nPUBLIC_ORIGIN=https://emprestimofacil.pro\n'
                         'DATA_ENCRYPTION_KEY=preserve-original-key\nAPPLICATIONS_ENABLED=false\n'
                         'CAKTO_PAYMENTS_ENABLED=false\nEMAIL_PROVIDER=resend\nRESEND_API_KEY=\n')
        self.target.write_text(self.original, encoding='utf-8')
        self.target.chmod(0o600)

    def write_source(self, text):
        self.source.write_text(text, encoding='utf-8')
        self.source.chmod(0o600)

    def test_validate_without_writing_or_exposing_secrets(self):
        secret = 're_synthetic-private-key'
        self.write_source('RESEND_API_KEY=' + secret + '\nEMAIL_FROM=contato@emprestimofacil.pro\n')
        result = import_settings(self.target, self.source)
        self.assertNotIn(secret, json.dumps(result))
        self.assertFalse(result['applied'])
        self.assertTrue(result['configured']['RESEND_API_KEY'])
        self.assertEqual(self.original, self.target.read_text(encoding='utf-8'))

    def test_reject_protected_and_activation_variables(self):
        for key in ['APPLICATIONS_ENABLED', 'DATA_ENCRYPTION_KEY', 'PUBLIC_ORIGIN', 'CAKTO_PAYMENTS_ENABLED']:
            with self.subTest(key=key):
                self.write_source(key + '=changed\n')
                with self.assertRaises(ConfigurationError):
                    import_settings(self.target, self.source)
                self.assertEqual(self.original, self.target.read_text(encoding='utf-8'))

    def test_reject_wrong_sender_invalid_modes_short_secrets_and_duplicate_keys(self):
        values = ['EMAIL_FROM=admin@gmail.com', 'EMAIL_FROM=ef@resend.dev',
                  'EMAIL_PROVIDER=mock', 'BELVO_ENVIRONMENT=mock',
                  'TWILIO_ACCOUNT_SID=ACinvalid', 'CAKTO_WEBHOOK_SECRET=short',
                  'COMPANY_TERMS_URL=http://emprestimofacil.pro/termos',
                  'COMPANY_TERMS_URL=https://other.example/termos',
                  'RESEND_API_KEY=first\nRESEND_API_KEY=second']
        for value in values:
            with self.subTest(value=value.split('=')[0]):
                self.write_source(value + '\n')
                with self.assertRaises(ConfigurationError):
                    import_settings(self.target, self.source)

    @unittest.skipUnless(os.name == 'posix', 'Linux private permission and atomic replacement test')
    def test_apply_keeps_crypto_and_flags_and_creates_private_backup(self):
        self.write_source('RESEND_API_KEY=re_synthetic-private-key\nEMAIL_FROM=ef@emprestimofacil.pro\n')
        result = import_settings(self.target, self.source, apply=True)
        current = parse(self.target.read_text())
        self.assertTrue(result['applied'])
        self.assertEqual(current['DATA_ENCRYPTION_KEY'], 'preserve-original-key')
        self.assertEqual(current['APPLICATIONS_ENABLED'], 'false')
        self.assertEqual(current['CAKTO_PAYMENTS_ENABLED'], 'false')
        backups = list(self.target.parent.glob('backend.env.backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), self.original)
        for path in [self.target, backups[0]]:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(os.name == 'posix', 'Linux private permission test')
    def test_reject_group_or_world_readable_input(self):
        self.write_source('RESEND_API_KEY=re_synthetic-private-key\n')
        self.source.chmod(0o644)
        with self.assertRaises(ConfigurationError):
            import_settings(self.target, self.source)


if __name__ == '__main__':
    unittest.main()
