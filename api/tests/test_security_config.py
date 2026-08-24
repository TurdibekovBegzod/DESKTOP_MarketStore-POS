import unittest

from app.config import Settings


class SecurityConfigTest(unittest.TestCase):
    def test_ngrok_hostname_is_added_to_trusted_hosts(self):
        settings = Settings(
            trusted_hosts="localhost,127.0.0.1,api",
            ngrok_domain="https://marketstore-example.ngrok-free.app",
        )

        self.assertEqual(
            settings.resolved_trusted_hosts(),
            ["localhost", "127.0.0.1", "api", "marketstore-example.ngrok-free.app"],
        )

    def test_duplicate_hosts_are_removed(self):
        settings = Settings(
            trusted_hosts="localhost,localhost,api",
            ngrok_domain="api",
        )

        self.assertEqual(settings.resolved_trusted_hosts(), ["localhost", "api"])


if __name__ == "__main__":
    unittest.main()
