"""The server refuses a change made from a copy that has moved on.

Only that one row: refusing the whole batch would turn one stale product edit
into a failed sale. And a row the server has never seen carries no version to
disagree with, which is why an insert can never be refused.
"""

import unittest

from app.routers.sync import _upsert_record
from app.schemas import PushResponse, RecordIn, RejectedRecordOut


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    """Stands in for the database; records what was asked of it."""

    def __init__(self, written=True):
        self.statements = []
        self._written = written

    def execute(self, statement):
        self.statements.append(statement)
        return _FakeResult(("id",) if self._written else None)


class _User:
    id = 1
    uid = "acct-1"


class StaleWriteProtectionTest(unittest.TestCase):
    @staticmethod
    def _record(**overrides):
        payload = {
            "table_name": "products",
            "local_id": "3f6a2f70-0000-4000-8000-000000000001",
            "data": {"id": "3f6a2f70-0000-4000-8000-000000000001", "name": "Mahsulot"},
        }
        payload.update(overrides)
        return RecordIn(**payload)

    def test_a_row_without_a_claimed_version_is_always_written(self):
        session = _FakeSession(written=False)

        accepted = _upsert_record(session, _User(), self._record())

        self.assertTrue(accepted, "an insert must never be refused")
        self.assertEqual(len(session.statements), 1)

    def test_a_claimed_version_that_still_matches_is_written(self):
        session = _FakeSession(written=True)

        accepted = _upsert_record(session, _User(), self._record(expected_version=4))

        self.assertTrue(accepted)

    def test_a_claimed_version_that_has_moved_on_is_refused(self):
        session = _FakeSession(written=False)

        accepted = _upsert_record(session, _User(), self._record(expected_version=4))

        self.assertFalse(accepted)

    def test_the_claim_travels_on_the_wire(self):
        record = self._record(expected_version=9)
        self.assertEqual(record.expected_version, 9)
        self.assertIsNone(self._record().expected_version)

    def test_a_refusal_is_reported_alongside_what_was_saved(self):
        response = PushResponse(
            saved=3,
            batch_id=1,
            generation=7,
            rejected=[RejectedRecordOut(
                table_name="products",
                local_id="3f6a2f70-0000-4000-8000-000000000001",
                expected_version=4,
                server_version=6,
            )],
        )

        self.assertEqual(response.saved, 3)
        self.assertEqual(response.rejected[0].server_version, 6)
        # Older clients simply do not send it, and get an empty list back.
        self.assertEqual(PushResponse(saved=1, batch_id=1).rejected, [])


if __name__ == "__main__":
    unittest.main()
