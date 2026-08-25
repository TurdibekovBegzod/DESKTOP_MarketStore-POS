"""Tests for the release-published ping and the stored-release fast path."""

import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.releases import _parse_published_at, _slim_assets, normalize_version
from app.routers import updates as updates_router
from app.routers.updates import ReleasePing, _stored_release_response


class ReleaseHelpersTest(unittest.TestCase):
    def test_tag_is_normalized_to_a_bare_version(self):
        self.assertEqual(normalize_version("v1.0.5"), "1.0.5")
        self.assertEqual(normalize_version("  release-2.10.0 "), "2.10.0")
        self.assertEqual(normalize_version("1.0.0"), "1.0.0")
        self.assertEqual(normalize_version(""), "")

    def test_assets_are_slimmed_to_what_the_updater_needs(self):
        slim = _slim_assets([
            {"id": 1, "name": "Setup.exe", "size": 10, "digest": "sha256:ab",
             "browser_download_url": "https://x/y", "uploader": {"login": "bot"}},
            "not-a-dict",
        ])
        self.assertEqual(len(slim), 1)
        self.assertEqual(set(slim[0]), {"id", "name", "size", "digest", "browser_download_url"})

    def test_published_at_parsing_is_forgiving(self):
        self.assertIsNone(_parse_published_at(None))
        self.assertIsNone(_parse_published_at("not a date"))
        parsed = _parse_published_at("2026-08-25T12:00:00Z")
        self.assertEqual(parsed.year, 2026)
        now = datetime.now(timezone.utc)
        self.assertIs(_parse_published_at(now), now)


class StoredReleaseResponseTest(unittest.TestCase):
    def _row(self, version="1.0.5"):
        return SimpleNamespace(
            tag=f"v{version}",
            version=version,
            name=f"MarketStore POS v{version}",
            notes="Yaxshilanishlar",
            published_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
            assets=[{
                "id": 42, "name": "MarketStore_Setup_1.0.5.exe", "size": 6000000,
                "digest": "sha256:" + "a" * 64,
                "browser_download_url": "https://github.com/x/y/releases/download/v1.0.5/s.exe",
            }],
            source="ping",
        )

    def test_answers_without_calling_github(self):
        with patch.object(updates_router, "get_release", return_value=self._row()):
            result = _stored_release_response(object(), "windows", "1.0.4")
        self.assertTrue(result["has_update"])
        self.assertEqual(result["latest_version"], "1.0.5")
        self.assertEqual(result["asset_id"], 42)
        self.assertEqual(result["file_size"], 6000000)
        self.assertEqual(result["sha256"], "a" * 64)
        self.assertTrue(result["download_url"].endswith("asset_id=42"))
        self.assertTrue(result["direct_download_url"].startswith("https://github.com/"))

    def test_same_version_is_not_an_update(self):
        with patch.object(updates_router, "get_release", return_value=self._row("1.0.4")):
            result = _stored_release_response(object(), "windows", "1.0.4")
        self.assertFalse(result["has_update"])

    def test_newer_installed_build_is_not_downgraded(self):
        with patch.object(updates_router, "get_release", return_value=self._row("1.0.5")):
            result = _stored_release_response(object(), "windows", "1.1.0")
        self.assertFalse(result["has_update"])

    def test_an_update_without_a_usable_asset_falls_through_to_github(self):
        """A tokenless server may store a tag it could not enrich.

        Returning it anyway would show "update available" with an empty
        download URL, so the GitHub path has to get a chance instead.
        """
        row = self._row()
        row.assets = []
        with patch.object(updates_router, "get_release", return_value=row):
            self.assertIsNone(_stored_release_response(object(), "windows", "1.0.4"))

    def test_no_asset_for_this_platform_falls_through_too(self):
        row = self._row()
        row.assets = [{"id": 7, "name": "MarketStore-1.0.5.AppImage", "size": 1}]
        with patch.object(updates_router, "get_release", return_value=row):
            self.assertIsNone(_stored_release_response(object(), "windows", "1.0.4"))
        # ...but Linux, which that asset is for, is answered without GitHub.
        with patch.object(updates_router, "get_release", return_value=row):
            self.assertTrue(_stored_release_response(object(), "linux", "1.0.4")["has_update"])

    def test_up_to_date_needs_no_asset_at_all(self):
        """The common case: no update, so nothing to download, so no GitHub."""
        row = self._row("1.0.4")
        row.assets = []
        with patch.object(updates_router, "get_release", return_value=row):
            result = _stored_release_response(object(), "windows", "1.0.4")
        self.assertIsNotNone(result)
        self.assertFalse(result["has_update"])

    def test_no_stored_release_falls_through_to_the_github_path(self):
        with patch.object(updates_router, "get_release", return_value=None):
            self.assertIsNone(_stored_release_response(object(), "windows", "1.0.4"))


class ReleasePingAuthTest(unittest.TestCase):
    def _call(self, secret_header, configured_secret="s3cret", **kwargs):
        settings = SimpleNamespace(release_ping_secret=configured_secret)
        with patch.object(updates_router, "get_settings", return_value=settings), \
             patch.object(updates_router, "fetch_latest_from_github", **kwargs) as fetch, \
             patch.object(updates_router, "store_release") as store, \
             patch.object(updates_router, "broadcast_release") as broadcast:
            result = asyncio.run(updates_router.release_published(
                ReleasePing(tag="v1.0.5"), x_release_secret=secret_header, db=object(),
            ))
        return result, fetch, store, broadcast

    def test_rejects_a_wrong_secret(self):
        with self.assertRaises(HTTPException) as ctx:
            self._call("wrong", return_value=None)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_rejects_a_missing_secret(self):
        with self.assertRaises(HTTPException) as ctx:
            self._call(None, return_value=None)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_is_disabled_when_no_secret_is_configured(self):
        with self.assertRaises(HTTPException) as ctx:
            self._call("anything", configured_secret=None, return_value=None)
        self.assertEqual(ctx.exception.status_code, 503)

    def test_accepted_ping_stores_and_broadcasts(self):
        payload = {"type": "release", "tag": "v1.0.5", "latest_version": "1.0.5"}
        settings = SimpleNamespace(release_ping_secret="s3cret")
        github = {"tag_name": "v1.0.5", "name": "n", "body": "b", "assets": []}
        with patch.object(updates_router, "get_settings", return_value=settings), \
             patch.object(updates_router, "fetch_latest_from_github", return_value=github), \
             patch.object(updates_router, "store_release", return_value=payload) as store, \
             patch.object(updates_router, "broadcast_release") as broadcast:
            result = asyncio.run(updates_router.release_published(
                ReleasePing(tag="v1.0.5"), x_release_secret="s3cret", db="db",
            ))
        self.assertEqual(result["latest_version"], "1.0.5")
        store.assert_called_once_with("db", "v1.0.5", github, "ping")
        broadcast.assert_called_once_with(payload)

    def test_a_mismatched_github_answer_is_discarded_not_trusted(self):
        """The workflow knows which tag it just published; GitHub may lag."""
        payload = {"type": "release", "tag": "v1.0.5", "latest_version": "1.0.5"}
        settings = SimpleNamespace(release_ping_secret="s3cret")
        stale = {"tag_name": "v1.0.4", "name": "old", "assets": []}
        with patch.object(updates_router, "get_settings", return_value=settings), \
             patch.object(updates_router, "fetch_latest_from_github", return_value=stale), \
             patch.object(updates_router, "store_release", return_value=payload) as store, \
             patch.object(updates_router, "broadcast_release"):
            asyncio.run(updates_router.release_published(
                ReleasePing(tag="v1.0.5"), x_release_secret="s3cret", db="db",
            ))
        store.assert_called_once_with("db", "v1.0.5", None, "ping")

    def test_github_being_unreachable_does_not_block_the_ping(self):
        payload = {"type": "release", "tag": "v1.0.5", "latest_version": "1.0.5"}
        settings = SimpleNamespace(release_ping_secret="s3cret")
        with patch.object(updates_router, "get_settings", return_value=settings), \
             patch.object(updates_router, "fetch_latest_from_github", return_value=None), \
             patch.object(updates_router, "store_release", return_value=payload) as store, \
             patch.object(updates_router, "broadcast_release") as broadcast:
            asyncio.run(updates_router.release_published(
                ReleasePing(tag="v1.0.5"), x_release_secret="s3cret", db="db",
            ))
        store.assert_called_once_with("db", "v1.0.5", None, "ping")
        broadcast.assert_called_once()


if __name__ == "__main__":
    unittest.main()
