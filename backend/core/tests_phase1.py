"""Phase 1 deployment-settings contract tests."""
from django.conf import settings
from django.test import SimpleTestCase

from core.settings import _database_settings


class DeploymentSettingsContractTests(SimpleTestCase):
    def test_cors_origins_use_explicit_allowlist(self):
        origins = settings.CORS_ALLOWED_ORIGINS

        self.assertTrue(origins)
        self.assertNotIn('*', origins)
        self.assertNotIn('', origins)
        for origin in origins:
            self.assertTrue(origin.startswith(('http://', 'https://')))
            self.assertNotIn(' ', origin)

    def test_allowed_hosts_use_explicit_allowlist(self):
        hosts = settings.ALLOWED_HOSTS

        self.assertTrue(hosts)
        self.assertNotIn('*', hosts)
        self.assertNotIn('', hosts)

    def test_num_proxies_is_a_nonnegative_integer(self):
        num_proxies = settings.REST_FRAMEWORK['NUM_PROXIES']

        self.assertIsInstance(num_proxies, int)
        self.assertGreaterEqual(num_proxies, 0)

    def test_postgres_settings_reuse_connections(self):
        database = _database_settings(
            'postgres://user:p%40ss@db.example.com:6543/dpt'
        )

        self.assertEqual(database['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(database['NAME'], 'dpt')
        self.assertEqual(database['USER'], 'user')
        self.assertEqual(database['PASSWORD'], 'p@ss')
        self.assertEqual(database['HOST'], 'db.example.com')
        self.assertEqual(database['PORT'], 6543)
        self.assertGreaterEqual(database['CONN_MAX_AGE'], 30)
        self.assertLessEqual(database['CONN_MAX_AGE'], 60)

    def test_local_sqlite_settings_are_preserved(self):
        database = _database_settings('sqlite:///db.sqlite3')

        self.assertEqual(database['ENGINE'], 'django.db.backends.sqlite3')
        self.assertNotIn('CONN_MAX_AGE', database)
