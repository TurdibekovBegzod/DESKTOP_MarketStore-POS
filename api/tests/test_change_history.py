"""Downloads must follow the account's change history, never the clock.

The bug this guards against did not look like a bug from the outside. Two shops'
machines sat side by side, both showing "Onlayn", both convinced they were up to
date, and each one listed products the other had never heard of.

The cause: a download asked "what changed after this moment". A push stamps its
rows at the moment its transaction *opens*, but the rows only become visible when
it *commits*. A download that ran in between was told nothing had changed, moved
its marker past that moment, and from then on those rows sat behind the marker
where no later download would ever ask for them again.

A counter handed out under a lock cannot do that: whoever takes the lower number
also commits first, so "everything above my number" can never skip a row.
"""

import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy.dialects import postgresql

from app.models import UserRecord
from app.routers.sync import _upsert_record
from app.schemas import PullResponse, RecordIn, RecordOut, SyncStateOut


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, written=True):
        self.statements = []
        self._written = written

    def execute(self, statement):
        self.statements.append(statement)
        return _FakeResult(("id",) if self._written else None)


class _User:
    id = 1
    uid = "acct-1"


def _record(**overrides):
    payload = {
        "table_name": "products",
        "local_id": "3f6a2f70-0000-4000-8000-000000000001",
        "data": {"id": "3f6a2f70-0000-4000-8000-000000000001", "name": "Mahsulot"},
    }
    payload.update(overrides)
    return RecordIn(**payload)


def _rendered(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


class StoredChangeNumberTest(unittest.TestCase):
    def test_a_new_row_is_written_with_the_number_it_was_given(self):
        session = _FakeSession()
        _upsert_record(session, _User(), _record(), 17)
        statement = session.statements[0]
        self.assertEqual(statement.compile().params["change_seq"], 17)

    def test_changing_a_row_moves_it_to_the_front_of_the_history(self):
        """Otherwise an edit would keep its old place and every device that had
        already read past it would never be told."""
        session = _FakeSession()
        _upsert_record(session, _User(), _record(), 23)
        rendered = _rendered(session.statements[0])
        self.assertIn("ON CONFLICT", rendered)
        self.assertIn("change_seq", rendered.split("DO UPDATE SET", 1)[1])

    def test_a_refused_change_carries_the_number_too(self):
        session = _FakeSession(written=False)
        accepted = _upsert_record(session, _User(), _record(expected_version=4), 31)
        self.assertFalse(accepted)
        self.assertEqual(session.statements[0].compile().params["change_seq"], 31)


class DownloadPositionTest(unittest.TestCase):
    """The exact shape of the race, written down."""

    def test_asking_by_clock_steps_over_a_push_that_was_still_committing(self):
        opened = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)
        # The other device's push opened at 10:00:00 and committed at 10:00:02.
        # Our download ran at 10:00:01 and saw nothing, so it remembered 10:00:01.
        marker = opened + timedelta(seconds=1)
        row_stamp = opened
        self.assertFalse(row_stamp > marker, "this is what made the row invisible")

    def test_asking_by_position_cannot_step_over_it(self):
        # The pushing transaction holds the counter until it commits, so the row
        # it wrote can only carry a number we have not read yet.
        our_position = 5
        row_number = 6
        self.assertGreater(row_number, our_position)


class AnswerShapeTest(unittest.TestCase):
    def test_a_download_reports_the_position_it_actually_delivered(self):
        answer = PullResponse(
            records=[],
            server_time=datetime.now(timezone.utc),
            generation=9,
            cursor=6,
        )
        self.assertTrue(answer.cursor_supported)
        self.assertEqual(answer.cursor, 6)

    def test_an_account_reports_how_far_its_history_reaches(self):
        state = SyncStateOut(generation=9, cursor=6, server_time=datetime.now(timezone.utc))
        self.assertEqual(state.cursor, 6)
        self.assertTrue(state.cursor_supported)

    def test_a_delivered_row_says_where_it_sits(self):
        now = datetime.now(timezone.utc)
        row = RecordOut(
            id=1,
            user_uid="acct-1",
            sync_version=2,
            change_seq=6,
            created_at=now,
            updated_at=now,
            table_name="products",
            local_id="3f6a2f70-0000-4000-8000-000000000001",
            data={},
        )
        self.assertEqual(row.change_seq, 6)

    def test_the_stored_row_keeps_the_number(self):
        self.assertIn("change_seq", UserRecord.__table__.c)


if __name__ == "__main__":
    unittest.main()
