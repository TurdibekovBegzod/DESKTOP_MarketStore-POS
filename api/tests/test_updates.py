import base64
import hashlib
import unittest

from pydantic import ValidationError

from app.routers.updates import asset_sha256, is_newer_version, match_asset_for_platform
from app.schemas import PushRequest, RecordIn


class UpdateRouterTest(unittest.TestCase):
    def test_version_and_asset_helpers(self):
        self.assertTrue(is_newer_version("v1.2.0", "1.1.9"))
        self.assertFalse(is_newer_version("1.0.0", "1.0.0"))
        assets = [{"name": "README.txt"}, {"name": "MarketStore_Setup_1.2.0.exe", "id": 7}]
        self.assertEqual(match_asset_for_platform(assets, "windows")["id"], 7)
        self.assertIsNone(match_asset_for_platform([assets[0]], "windows"))

    def test_asset_sha256_rejects_invalid_digest(self):
        checksum = "b" * 64
        self.assertEqual(asset_sha256({"digest": f"sha256:{checksum}"}), checksum)
        self.assertIsNone(asset_sha256({"digest": "sha256:short"}))
        self.assertIsNone(asset_sha256(None))

    def test_sync_payload_rejects_unknown_tables_and_oversized_batches(self):
        with self.assertRaises(ValidationError):
            RecordIn(table_name="unknown_table", local_id="1")
        record = RecordIn(table_name="products", local_id="1")
        with self.assertRaises(ValidationError):
            PushRequest(records=[record] * 1001)

    def test_account_logo_payload_is_validated(self):
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            + (64).to_bytes(4, "big")
            + (64).to_bytes(4, "big")
            + b"small-test-logo"
        )
        encoded = base64.b64encode(png).decode("ascii")
        record = RecordIn(
            table_name="account_assets",
            local_id="desktop_logo",
            data={
                "id": "desktop_logo",
                "media_type": "image/png",
                "content_base64": encoded,
                "sha256": hashlib.sha256(png).hexdigest(),
            },
        )
        self.assertEqual(record.local_id, "desktop_logo")

        with self.assertRaises(ValidationError):
            RecordIn(
                table_name="account_assets",
                local_id="desktop_logo",
                data={
                    "id": "desktop_logo",
                    "media_type": "image/png",
                    "content_base64": encoded,
                    "sha256": "0" * 64,
                },
            )


if __name__ == "__main__":
    unittest.main()
