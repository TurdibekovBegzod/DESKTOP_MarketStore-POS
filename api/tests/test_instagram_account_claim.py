"""One Instagram account belongs to one shop.

Covers the ownership lookup, the claim-check endpoint the desktop app calls
before saving, and the sync-push guard that catches a device which skipped that
check (an offline save that syncs later, or an older build).
"""

import unittest
from unittest.mock import MagicMock, patch

from app.instagram_service import find_account_owners, find_conflicting_owner
from app.routers.instagram import _mask_email


class FakeOwnerSession:
    """Stands in for the ownership query, which is the only statement run."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.params = None

    def execute(self, statement, params=None):
        self.params = params
        result = MagicMock()
        result.all.return_value = self.rows
        return result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class AccountOwnerLookupTest(unittest.TestCase):
    def test_free_account_has_no_owners(self):
        session = FakeOwnerSession(rows=[])
        with patch("app.instagram_service.SessionLocal", return_value=session):
            self.assertEqual(find_account_owners("29087822314156401"), [])

    def test_owner_is_returned_with_email(self):
        session = FakeOwnerSession(rows=[(7, "uid-7", "shop@example.com")])
        with patch("app.instagram_service.SessionLocal", return_value=session):
            owners = find_account_owners("29087822314156401")

        self.assertEqual(len(owners), 1)
        self.assertEqual(owners[0].user_id, 7)
        self.assertEqual(owners[0].user_uid, "uid-7")
        self.assertEqual(owners[0].email, "shop@example.com")

    def test_blank_account_id_is_not_queried(self):
        session = FakeOwnerSession(rows=[(7, "uid-7", "shop@example.com")])
        with patch("app.instagram_service.SessionLocal", return_value=session):
            self.assertEqual(find_account_owners("   "), [])
        self.assertIsNone(session.params)

    def test_account_id_is_trimmed_before_matching(self):
        session = FakeOwnerSession(rows=[])
        with patch("app.instagram_service.SessionLocal", return_value=session):
            find_account_owners("  29087822314156401  ")
        self.assertEqual(session.params, {"account_id": "29087822314156401"})


class ConflictingOwnerTest(unittest.TestCase):
    def test_same_shop_resaving_is_not_a_conflict(self):
        """Re-entering the same credentials is how a token gets rotated."""
        session = FakeOwnerSession(rows=[(7, "uid-7", "shop@example.com")])
        with patch("app.instagram_service.SessionLocal", return_value=session):
            self.assertIsNone(find_conflicting_owner("29087822314156401", "uid-7"))

    def test_other_shop_claiming_the_account_is_a_conflict(self):
        session = FakeOwnerSession(rows=[(7, "uid-7", "first@example.com")])
        with patch("app.instagram_service.SessionLocal", return_value=session):
            owner = find_conflicting_owner("29087822314156401", "uid-99")

        self.assertIsNotNone(owner)
        self.assertEqual(owner.email, "first@example.com")

    def test_free_account_has_no_conflict(self):
        session = FakeOwnerSession(rows=[])
        with patch("app.instagram_service.SessionLocal", return_value=session):
            self.assertIsNone(find_conflicting_owner("29087822314156401", "uid-99"))

    def test_existing_duplicate_still_reports_the_other_shop(self):
        """Data written before this rule existed must not read as available."""
        session = FakeOwnerSession(rows=[
            (7, "uid-7", "first@example.com"),
            (8, "uid-8", "second@example.com"),
        ])
        with patch("app.instagram_service.SessionLocal", return_value=session):
            owner = find_conflicting_owner("29087822314156401", "uid-8")

        self.assertIsNotNone(owner)
        self.assertEqual(owner.user_uid, "uid-7")

    def test_lookup_failure_does_not_block_the_save(self):
        """A database error must not lock a shop out of its own settings."""
        with patch("app.instagram_service.SessionLocal", side_effect=RuntimeError("db down")):
            self.assertIsNone(find_conflicting_owner("29087822314156401", "uid-9"))


class MaskEmailTest(unittest.TestCase):
    def test_address_is_recognisable_but_not_disclosed(self):
        self.assertEqual(_mask_email("begzodasidev@gmail.com"), "be**********@gmail.com")

    def test_short_local_part_is_still_hidden(self):
        self.assertEqual(_mask_email("ab@gmail.com"), "a*@gmail.com")

    def test_missing_or_invalid_address_returns_none(self):
        self.assertIsNone(_mask_email(None))
        self.assertIsNone(_mask_email(""))
        self.assertIsNone(_mask_email("not-an-email"))


class SyncPushGuardTest(unittest.TestCase):
    """The sync path is the last line of defence for a device that skipped the check."""

    def setUp(self):
        from app.routers import sync

        self.sync = sync
        self.user = MagicMock(uid="uid-99", id=99)

    def _record(self, local_id="instagram_account_id", value="29087822314156401",
                table_name="app_settings", deleted_at=None):
        record = MagicMock()
        record.table_name = table_name
        record.local_id = local_id
        record.data = {"value": value}
        record.deleted_at = deleted_at
        return record

    def test_account_owned_by_another_shop_is_refused(self):
        owner = MagicMock(email="first@example.com", user_uid="uid-7")
        with patch("app.routers.sync.find_conflicting_owner", return_value=owner):
            reason = self.sync._instagram_claim_conflict(self.user, self._record())

        self.assertIsNotNone(reason)
        self.assertIn("29087822314156401", reason)
        self.assertIn("first@example.com", reason)

    def test_free_account_passes_through(self):
        with patch("app.routers.sync.find_conflicting_owner", return_value=None):
            self.assertIsNone(self.sync._instagram_claim_conflict(self.user, self._record()))

    def test_other_settings_rows_are_never_checked(self):
        with patch("app.routers.sync.find_conflicting_owner") as lookup:
            self.assertIsNone(
                self.sync._instagram_claim_conflict(self.user, self._record(local_id="theme"))
            )
            self.assertIsNone(
                self.sync._instagram_claim_conflict(self.user, self._record(table_name="products"))
            )
        lookup.assert_not_called()

    def test_clearing_the_account_is_always_allowed(self):
        """Wiping credentials is how a shop gets out of a conflict."""
        with patch("app.routers.sync.find_conflicting_owner") as lookup:
            self.assertIsNone(
                self.sync._instagram_claim_conflict(self.user, self._record(value=""))
            )
            self.assertIsNone(
                self.sync._instagram_claim_conflict(
                    self.user, self._record(deleted_at="2026-09-30T00:00:00Z")
                )
            )
        lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
