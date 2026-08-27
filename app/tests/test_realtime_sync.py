"""Tests for the realtime change stream and the Anki-style conflict flow."""

import io
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import api_client
import database as db
import sync_service


class _FakeStream:
    """Minimal file-like object that hands back a canned SSE body."""

    def __init__(self, text):
        self._buffer = io.BytesIO(text.encode("utf-8"))
        self.closed = False

    def readline(self):
        return self._buffer.readline()

    def close(self):
        self.closed = True


class SseParserTest(unittest.TestCase):
    def test_parses_named_events_and_ignores_comments(self):
        body = (
            ": keepalive comment\n"
            "event: hello\n"
            'data: {"generation": 3}\n'
            "\n"
            "event: change\n"
            'data: {"generation": 4, "tables": ["products"]}\n'
            "\n"
            "event: ping\n"
            'data: {"generation": 4}\n'
            "\n"
        )
        events = list(api_client.iter_sse_events(_FakeStream(body)))
        self.assertEqual([name for name, _ in events], ["hello", "change", "ping"])
        self.assertEqual(events[1][1]["generation"], 4)
        self.assertEqual(events[1][1]["tables"], ["products"])

    def test_multiline_data_is_joined(self):
        body = 'event: change\ndata: {"generation":\ndata: 7}\n\n'
        events = list(api_client.iter_sse_events(_FakeStream(body)))
        self.assertEqual(events, [("change", {"generation": 7})])

    def test_event_name_resets_between_events(self):
        body = 'event: change\ndata: {"a": 1}\n\ndata: {"b": 2}\n\n'
        events = list(api_client.iter_sse_events(_FakeStream(body)))
        self.assertEqual(events[0][0], "change")
        self.assertEqual(events[1][0], "message")

    def test_truncated_stream_stops_cleanly(self):
        body = 'event: change\ndata: {"generation": 9}\n'  # no terminating blank line
        self.assertEqual(list(api_client.iter_sse_events(_FakeStream(body))), [])


class _SyncDbTestCase(unittest.TestCase):
    def setUp(self):
        self.storage_root = tempfile.mkdtemp(prefix="marketstore-realtime-")
        self.old_path = db.DB_PATH
        self.old_active_uid = db._ACTIVE_ACCOUNT_UID
        self.old_backup_dir = db.BACKUP_DIR
        db.BACKUP_DIR = os.path.join(self.storage_root, "backups")
        db.activate_account_database("acct-1", email="one@example.com", storage_root=self.storage_root)
        db.init_db(
            account_owner={"user_uid": "acct-1", "email": "one@example.com", "display_name": "one"},
            seed_defaults=False,
        )
        self.owner = db.sync_online_user("one@example.com", role="admin", user_uid="acct-1", access_token="tok-1")

    def tearDown(self):
        if db._ENGINE is not None:
            db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        db.DB_PATH = self.old_path
        db._ACTIVE_ACCOUNT_UID = self.old_active_uid
        db.BACKUP_DIR = self.old_backup_dir
        shutil.rmtree(self.storage_root, ignore_errors=True)

    def _add_product(self, barcode, name):
        return db.add_product({
            "barcode": barcode, "name": name, "price": 100, "cost": 50,
            "stock": 2, "unit": "dona",
        })


class RemoteChangeStateTest(_SyncDbTestCase):
    def test_remote_flag_lights_up_and_clears(self):
        self.assertFalse(db.get_remote_change()["pending"])
        db.mark_remote_change(5, tables=["products"], device_key="desktop-other")
        remote = db.get_remote_change()
        self.assertTrue(remote["pending"])
        self.assertEqual(remote["generation"], 5)
        self.assertEqual(remote["tables"], ["products"])

        db.set_sync_generation(5)
        self.assertFalse(db.get_remote_change()["pending"])

    def test_stale_event_does_not_relight_the_badge(self):
        db.set_sync_generation(9)
        db.mark_remote_change(4, tables=["products"], device_key="desktop-other")
        self.assertFalse(db.get_remote_change()["pending"])


class ConflictDetectionTest(_SyncDbTestCase):
    def _describe(self, server_generation, server_records=42):
        state = {
            "generation": server_generation,
            "records_count": server_records,
            "last_change_at": "2026-08-25T10:00:00+00:00",
            "last_device_key": "desktop-other",
            "last_tables": ["products"],
        }
        return sync_service.describe_sync(self.owner, server_state=state)

    def test_no_conflict_when_only_the_server_moved(self):
        db.mark_sync_pushed()
        db.set_sync_generation(1)
        info = self._describe(2)
        self.assertFalse(info["conflict"])
        self.assertTrue(info["server_ahead"])

    def test_no_conflict_when_only_we_have_local_changes(self):
        db.set_sync_generation(1)
        self._add_product("P-1", "Local only")
        info = self._describe(1)
        self.assertFalse(info["conflict"])
        self.assertGreater(info["local_pending"], 0)

    def test_conflict_when_both_sides_moved(self):
        db.set_sync_generation(1)
        self._add_product("P-1", "Local change")
        info = self._describe(2)
        self.assertTrue(info["conflict"])
        self.assertGreater(info["local_pending"], 0)
        self.assertEqual(info["server_generation"], 2)
        self.assertEqual(info["server_records"], 42)

    def test_push_raises_sync_conflict_when_server_rejects(self):
        db.set_sync_generation(1)
        self._add_product("P-1", "Local change")

        def fake_push(*_args, **_kwargs):
            raise api_client.SyncConflictError("changed", server_generation=7, expected_generation=1)

        state = {"generation": 7, "records_count": 3, "last_tables": [], "last_device_key": "other"}
        with patch.object(api_client, "push_sync_records", side_effect=fake_push), \
             patch.object(api_client, "get_sync_state", return_value=state):
            with self.assertRaises(sync_service.SyncConflict) as ctx:
                sync_service.push_local_changes(self.owner)
        self.assertTrue(ctx.exception.info["conflict"])
        self.assertEqual(ctx.exception.info["server_generation"], 7)

    def test_empty_push_does_not_swallow_a_pending_download(self):
        """A push with nothing to send must not pretend we have seen server data."""
        db.mark_sync_pushed()
        db.set_sync_generation(1)
        db.mark_remote_change(4, tables=["sales"], device_key="desktop-other")
        with patch.object(api_client, "get_sync_state", return_value={"generation": 4}):
            sync_service.push_local_changes(self.owner)
        self.assertEqual(db.get_sync_generation(), 1)
        self.assertTrue(db.get_remote_change()["pending"])


class DestructiveResolutionTest(_SyncDbTestCase):
    def test_wipe_keeps_the_signed_in_account_user(self):
        db.add_user("cashier@example.com", role="cashier", username="Cashier")
        self._add_product("P-1", "Doomed")
        self.assertGreater(db.count_sync_records(), 0)

        db.wipe_sync_tables()

        self.assertEqual(db.get_all_products(), [])
        # The account owner must survive: its user_settings row holds the API
        # token, and the cascade would take it down with the user.
        self.assertEqual(db.get_user_api_token(self.owner["id"]), "tok-1")

    def test_remote_purge_erases_rows_outbox_and_account_backups_once(self):
        self._add_product("P-ERASE", "Erase me")
        backup_path = db.create_local_backup(tag="before_purge")
        self.assertTrue(os.path.exists(backup_path))
        self.assertGreater(db.get_sync_status()["pending_change_count"], 0)

        result = db.apply_remote_purge(7, server_generation=9)

        self.assertTrue(result["applied"])
        self.assertEqual(db.get_all_products(), [])
        self.assertEqual(db.get_sync_status()["pending_change_count"], 0)
        self.assertEqual(db.get_sync_generation(), 9)
        self.assertEqual(db.get_applied_purge_generation(), 7)
        self.assertEqual(db.get_user_api_token(self.owner["id"]), "tok-1")
        self.assertFalse(os.path.exists(backup_path))

        # Replayed SSE greetings must not erase legitimate post-purge work.
        self._add_product("P-NEW", "Created later")
        repeated = db.apply_remote_purge(7, server_generation=9)
        self.assertFalse(repeated["applied"])
        self.assertEqual([row["name"] for row in db.get_all_products()], ["Created later"])

    def test_push_applies_server_purge_before_exporting_local_rows(self):
        self._add_product("P-OLD", "Offline copy")
        state = {
            "generation": 12,
            "purge_generation": 12,
            "records_count": 0,
            "last_tables": ["products"],
            "last_device_key": "superadmin",
        }
        with patch.object(api_client, "get_sync_state", return_value=state), \
             patch.object(api_client, "push_sync_records") as push:
            result = sync_service.push_local_changes(self.owner)

        self.assertTrue(result["purged"])
        self.assertEqual(db.get_all_products(), [])
        push.assert_not_called()

    def test_force_download_backs_up_then_replaces_local_data(self):
        self._add_product("P-LOCAL", "Only local")
        server_records = [{
            "table_name": "products",
            "local_id": "9001",
            "data": {
                "id": 9001, "barcode": "P-SERVER", "name": "From server",
                "price": 500, "cost": 250, "stock": 4, "unit": "dona",
            },
            "local_updated_at": "2026-08-25 10:00:00",
            "deleted_at": None,
        }]
        with patch.object(
            api_client, "pull_sync_records",
            return_value={"records": server_records, "server_time": None, "generation": 11},
        ), patch.object(
            api_client,
            "get_sync_state",
            return_value={"generation": 11, "purge_generation": 0},
        ):
            result = sync_service.force_download(self.owner)

        self.assertEqual(result["direction"], "download")
        self.assertTrue(os.path.exists(result["backup_path"]))
        names = sorted(product["name"] for product in db.get_all_products())
        self.assertEqual(names, ["From server"])
        self.assertEqual(db.get_sync_generation(), 11)
        self.assertFalse(db.get_remote_change()["pending"])
        self.assertEqual(int(db.get_sync_status()["pending_change_count"] or 0), 0)

    def test_force_upload_snapshots_the_server_copy_before_resetting_it(self):
        self._add_product("P-LOCAL", "Mine wins")
        server_records = [{"table_name": "products", "local_id": "77", "data": {"id": 77, "name": "Server copy"}}]
        calls = {"reset": 0, "pushed": []}

        def fake_reset(_token, device_key=None, timeout=60, applied_purge_generation=None):
            calls["reset"] += 1
            self.assertEqual(applied_purge_generation, 0)
            return {"removed": 1, "generation": 12}

        def fake_push(_token, records, **kwargs):
            calls["pushed"].append(len(records))
            self.assertIsNone(kwargs.get("expected_generation"))
            return {"saved": len(records), "batch_id": 1, "generation": 13}

        with patch.object(api_client, "pull_sync_records", return_value={"records": server_records, "generation": 12}), \
             patch.object(api_client, "reset_sync_records", side_effect=fake_reset), \
             patch.object(api_client, "push_sync_records", side_effect=fake_push), \
             patch.object(api_client, "get_sync_state", return_value={"generation": 13}):
            result = sync_service.force_upload(self.owner)

        self.assertEqual(calls["reset"], 1)
        self.assertTrue(calls["pushed"])
        self.assertEqual(result["direction"], "upload")
        self.assertTrue(os.path.exists(result["backup_path"]))
        self.assertTrue(os.path.exists(result["server_backup_path"]))
        with __import__("gzip").open(result["server_backup_path"], "rb") as handle:
            saved = json.loads(handle.read().decode("utf-8"))
        self.assertEqual(saved["records"], server_records)
        self.assertEqual(db.get_sync_generation(), 13)


class LogoRealtimeTest(_SyncDbTestCase):
    def test_asset_only_pull_updates_the_shared_logo(self):
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        )
        import base64, hashlib
        record = {
            "table_name": "account_assets",
            "local_id": "desktop_logo",
            "data": {
                "id": "desktop_logo",
                "media_type": "image/png",
                "content_base64": base64.b64encode(png).decode("ascii"),
                "sha256": hashlib.sha256(png).hexdigest(),
                "updated_at": "2026-08-25 10:00:00",
            },
            "local_updated_at": "2026-08-25 10:00:00",
            "deleted_at": None,
        }
        with patch.object(api_client, "pull_sync_records", return_value={"records": [record], "generation": 5}):
            result = sync_service.refresh_account_assets(self.owner)

        self.assertEqual(result["imported"], 1)
        asset = db.get_account_asset("desktop_logo")
        self.assertIsNotNone(asset)
        self.assertEqual(asset["content"], png)
        # A single-table pull must not claim we have seen everything else.
        self.assertEqual(db.get_sync_generation(), 0)

    def test_local_asset_edit_defers_the_remote_refresh(self):
        db.save_account_asset("desktop_logo", b"\x89PNG\r\n\x1a\nlocal", "image/png")
        result = sync_service.refresh_account_assets(self.owner)
        self.assertEqual(result.get("skipped"), "local_changes_pending")


class ReleaseStateTest(_SyncDbTestCase):
    def test_known_release_round_trips(self):
        self.assertEqual(db.get_known_release()["version"], "")
        db.set_known_release("1.0.5", tag="v1.0.5", published_at="2026-08-25T12:00:00+00:00")
        known = db.get_known_release()
        self.assertEqual(known["version"], "1.0.5")
        self.assertEqual(known["tag"], "v1.0.5")

    def test_blank_version_is_ignored(self):
        db.set_known_release("1.0.5", tag="v1.0.5")
        db.set_known_release("")
        db.set_known_release(None)
        self.assertEqual(db.get_known_release()["version"], "1.0.5")


class ReleaseBadgeRuleTest(unittest.TestCase):
    """The dot must follow the installed build, not whether a dialog was seen."""

    def test_only_a_strictly_newer_build_lights_the_dot(self):
        from updater import is_newer_version

        self.assertTrue(is_newer_version("1.0.5", "1.0.4"))
        self.assertTrue(is_newer_version("v1.1.0", "1.0.9"))
        self.assertFalse(is_newer_version("1.0.4", "1.0.4"))
        # After the user updates, the stored release is no longer newer, so the
        # dot clears on its own without anyone having to dismiss it.
        self.assertFalse(is_newer_version("1.0.4", "1.0.5"))


class ReleaseEventDispatchTest(unittest.TestCase):
    """The listener must turn both wire shapes into a release signal."""

    def _drain(self, body):
        from realtime import SyncEventListener

        listener = SyncEventListener(lambda: "tok", lambda: 0)
        seen = {"release": [], "hello": [], "change": []}
        listener.release_available.connect(lambda p: seen["release"].append(p))
        listener.server_hello.connect(lambda p: seen["hello"].append(p))
        listener.remote_change.connect(lambda p: seen["change"].append(p))

        stream = _FakeStream(body)
        with patch.object(api_client, "open_sync_event_stream", return_value=stream):
            # One connection, then stop so the reconnect loop does not spin.
            original = api_client.iter_sse_events

            def once(response):
                for item in original(response):
                    yield item
                listener.stop()

            with patch.object(api_client, "iter_sse_events", side_effect=once):
                listener.run()
        return seen

    def test_release_event_is_emitted(self):
        body = (
            "event: release\n"
            'data: {"tag": "v1.0.5", "latest_version": "1.0.5"}\n'
            "\n"
        )
        seen = self._drain(body)
        self.assertEqual(len(seen["release"]), 1)
        self.assertEqual(seen["release"][0]["latest_version"], "1.0.5")

    def test_release_carried_inside_hello_is_emitted_too(self):
        body = (
            "event: hello\n"
            'data: {"generation": 3, "release": {"tag": "v1.0.5", "latest_version": "1.0.5"}}\n'
            "\n"
        )
        seen = self._drain(body)
        self.assertEqual(len(seen["hello"]), 1)
        self.assertEqual(len(seen["release"]), 1)
        self.assertEqual(seen["release"][0]["latest_version"], "1.0.5")

    def test_hello_without_a_release_emits_nothing_extra(self):
        body = 'event: hello\ndata: {"generation": 3, "release": null}\n\n'
        seen = self._drain(body)
        self.assertEqual(seen["release"], [])
        self.assertEqual(len(seen["hello"]), 1)


if __name__ == "__main__":
    unittest.main()
