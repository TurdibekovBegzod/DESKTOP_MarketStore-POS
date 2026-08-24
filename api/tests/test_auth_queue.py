import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from app.routers.auth import _enqueue_email_or_invalidate


class _FakeDb:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


class AuthEmailQueueTest(unittest.TestCase):
    def test_failed_queue_invalidates_code_for_immediate_retry(self):
        db = _FakeDb()
        code_row = SimpleNamespace(used_at=None)
        task = SimpleNamespace(delay=lambda *_args: (_ for _ in ()).throw(ConnectionError("redis down")))

        with self.assertRaises(HTTPException) as raised:
            _enqueue_email_or_invalidate(db, code_row, task, "user@example.com", "123456")

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIsNotNone(code_row.used_at)
        self.assertEqual(db.commits, 1)

    def test_successful_queue_keeps_code_active(self):
        db = _FakeDb()
        code_row = SimpleNamespace(used_at=None)
        calls = []
        task = SimpleNamespace(delay=lambda *args: calls.append(args))

        _enqueue_email_or_invalidate(db, code_row, task, "user@example.com", "123456")

        self.assertEqual(calls, [("user@example.com", "123456")])
        self.assertIsNone(code_row.used_at)
        self.assertEqual(db.commits, 0)


if __name__ == "__main__":
    unittest.main()
