"""The log archive is what makes "what happened last Tuesday" answerable.

Docker keeps a few megabytes per container and then throws the rest away, so
the collector copies every line into one file per month. These tests pin the
parts a viewer depends on: the newest lines come back first, scrolling back
walks the file instead of re-reading it, a finished month is compressed but
still readable, and the panel's endpoints stay behind the superadmin login.
"""

import gzip
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-000")
os.environ.setdefault("TRUSTED_HOSTS", "localhost,127.0.0.1,testserver")

from fastapi.testclient import TestClient  # noqa: E402

from app import log_archive  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.log_collector import demultiplex, split_timestamp  # noqa: E402
from app.main import app  # noqa: E402


def frame(stream: int, text: str) -> bytes:
    payload = text.encode("utf-8")
    return bytes([stream, 0, 0, 0]) + len(payload).to_bytes(4, "big") + payload


class LogArchiveTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("LOG_ARCHIVE_DIR")
        os.environ["LOG_ARCHIVE_DIR"] = self.directory.name
        get_settings.cache_clear()
        self.month = log_archive.month_key()

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("LOG_ARCHIVE_DIR", None)
        else:
            os.environ["LOG_ARCHIVE_DIR"] = self.previous
        get_settings.cache_clear()
        self.directory.cleanup()

    def _write(self, month: str, count: int, container: str = "marketstore-api"):
        path = log_archive.month_path(month)
        with path.open("a", encoding="utf-8") as handle:
            for index in range(count):
                handle.write(
                    log_archive.encode_line(
                        f"2026-08-28T06:{index // 60:02d}:{index % 60:02d}.000Z",
                        container,
                        "stderr" if index % 10 == 0 else "stdout",
                        f"qator {index}",
                    )
                    + "\n"
                )
        return path

    def test_a_line_survives_the_round_trip_with_its_awkward_characters(self):
        raw = log_archive.encode_line("2026-08-28T06:00:00.000Z", "api", "stdout", 'a\tb "c" \\ d')
        entry = log_archive.decode_line(raw)

        self.assertEqual(entry["m"], 'a\tb "c" \\ d')
        self.assertEqual(entry["c"], "api")
        self.assertNotIn("\n", raw)

    def test_a_runaway_message_is_cut_rather_than_filling_the_disk(self):
        entry = log_archive.decode_line(
            log_archive.encode_line("2026-08-28T06:00:00.000Z", "api", "stdout", "x" * 50_000)
        )

        self.assertLess(len(entry["m"]), 50_000)
        self.assertTrue(entry["m"].endswith("[qisqartirildi]"))

    def test_the_newest_lines_come_back_first_and_scrolling_walks_backwards(self):
        self._write(self.month, 900)

        page = log_archive.read_page(self.month, limit=100)
        self.assertEqual(len(page["lines"]), 100)
        self.assertEqual(page["lines"][-1]["m"], "qator 899")
        self.assertTrue(page["has_more"])

        older = log_archive.read_page(self.month, limit=100, before=page["next_before"])
        self.assertEqual(len(older["lines"]), 100)
        # The two pages meet without a gap and without repeating a line.
        self.assertEqual(older["lines"][-1]["m"], "qator 799")

    def test_reading_stops_cleanly_at_the_start_of_the_month(self):
        self._write(self.month, 12)

        page = log_archive.read_page(self.month, limit=100)

        self.assertEqual(len(page["lines"]), 12)
        self.assertFalse(page["has_more"])
        self.assertIsNone(page["next_before"])

    def test_a_container_filter_and_a_search_narrow_the_page(self):
        self._write(self.month, 20, container="marketstore-api")
        self._write(self.month, 20, container="marketstore-postgres")

        only_db = log_archive.read_page(self.month, limit=50, container="marketstore-postgres")
        self.assertEqual({line["c"] for line in only_db["lines"]}, {"marketstore-postgres"})

        found = log_archive.read_page(self.month, limit=50, query="qator 7")
        self.assertTrue(found["lines"])
        self.assertTrue(all("qator 7" in line["m"] for line in found["lines"]))

    def test_the_live_tail_only_returns_whole_lines(self):
        self._write(self.month, 3)
        entries, offset = log_archive.read_since(0)
        self.assertEqual(len(entries), 3)

        # A line caught halfway through being written is left for the next poll.
        path = log_archive.month_path(self.month)
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"t":"2026-08-28T06:10:00.000Z","c":"api","s":"stdout","m":"yarim')
        nothing, held = log_archive.read_since(offset)
        self.assertEqual(nothing, [])
        self.assertEqual(held, offset)

        with path.open("a", encoding="utf-8") as handle:
            handle.write('"}\n')
        finished, _ = log_archive.read_since(held)
        self.assertEqual([item["m"] for item in finished], ["yarim"])

    def test_a_finished_month_is_compressed_and_still_readable(self):
        self._write("2026-07", 40)
        self._write(self.month, 5)

        packed = log_archive.compress_finished_months(datetime(2026, 8, 28, tzinfo=timezone.utc))

        self.assertEqual(packed, ["2026-07"])
        self.assertFalse(log_archive.month_path("2026-07").exists())
        self.assertTrue(log_archive.month_path("2026-07", compressed=True).exists())

        page = log_archive.read_page("2026-07", limit=10)
        self.assertEqual(len(page["lines"]), 10)
        self.assertEqual(page["lines"][-1]["m"], "qator 39")
        self.assertTrue(page["has_more"])

    def test_the_month_in_progress_is_never_compressed(self):
        self._write(self.month, 5)

        log_archive.compress_finished_months()

        self.assertTrue(log_archive.month_path(self.month).exists())
        months = [item["month"] for item in log_archive.list_months()]
        self.assertIn(self.month, months)

    def test_retention_keeps_everything_unless_a_limit_is_set(self):
        for month in ("2026-05", "2026-06", "2026-07"):
            self._write(month, 3)
        self._write(self.month, 3)

        log_archive.compress_finished_months(datetime(2026, 8, 28, tzinfo=timezone.utc))
        self.assertEqual(len(log_archive.list_months()), 4)

        os.environ["LOG_RETENTION_MONTHS"] = "2"
        get_settings.cache_clear()
        log_archive.compress_finished_months(datetime(2026, 8, 28, tzinfo=timezone.utc))
        os.environ.pop("LOG_RETENTION_MONTHS", None)
        get_settings.cache_clear()

        kept = [item["month"] for item in log_archive.list_months()]
        self.assertEqual(kept, [self.month, "2026-07"])

    def test_a_missing_month_is_empty_rather_than_an_error(self):
        page = log_archive.read_page("2019-01", limit=10)

        self.assertEqual(page["lines"], [])
        self.assertFalse(page["has_more"])


class DockerStreamTest(unittest.TestCase):
    def test_stdout_and_stderr_are_told_apart(self):
        payload = frame(1, "2026-08-28T06:00:00.1Z hammasi joyida\n") + frame(
            2, "2026-08-28T06:00:01.2Z xatolik\n"
        )

        pieces = demultiplex(payload)

        self.assertEqual([stream for stream, _ in pieces], ["stdout", "stderr"])
        self.assertIn("xatolik", pieces[1][1])

    def test_a_timestamp_is_split_off_but_plain_text_is_left_alone(self):
        moment, message = split_timestamp("2026-08-28T06:00:00.123456789Z ishga tushdi")
        self.assertEqual(moment, "2026-08-28T06:00:00.123456789Z")
        self.assertEqual(message, "ishga tushdi")

        moment, message = split_timestamp("vaqtsiz qator")
        self.assertEqual(moment, "")
        self.assertEqual(message, "vaqtsiz qator")

    def test_a_container_without_a_tty_header_is_not_dropped(self):
        pieces = demultiplex(b"oddiy matn\n")

        self.assertEqual(len(pieces), 1)
        self.assertIn("oddiy matn", pieces[0][1])


class LogEndpointTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous_dir = os.environ.get("LOG_ARCHIVE_DIR")
        self.previous_password = os.environ.get("SUPERADMIN_PASSWORD")
        os.environ["LOG_ARCHIVE_DIR"] = self.directory.name
        os.environ["SUPERADMIN_PASSWORD"] = "hunter2"
        get_settings.cache_clear()
        self.client = TestClient(app)
        month = log_archive.month_key()
        with log_archive.month_path(month).open("w", encoding="utf-8") as handle:
            handle.write(
                log_archive.encode_line(
                    "2026-08-28T06:00:00.000Z", "marketstore-api", "stdout", "salom"
                )
                + "\n"
            )

    def tearDown(self):
        for key, value in (
            ("LOG_ARCHIVE_DIR", self.previous_dir),
            ("SUPERADMIN_PASSWORD", self.previous_password),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()
        self.directory.cleanup()

    def _token(self) -> str:
        response = self.client.post(
            "/api/v1/superadmin/login", json={"username": "superadmin", "password": "hunter2"}
        )
        return response.json()["access_token"]

    def test_the_logs_are_not_readable_without_the_control_panel_login(self):
        for path in ("/api/v1/superadmin/logs", "/api/v1/superadmin/logs/months"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)

    def test_the_stream_refuses_a_missing_or_wrong_token(self):
        self.assertEqual(
            self.client.get("/api/v1/superadmin/logs/stream").status_code, 401
        )
        self.assertEqual(
            self.client.get("/api/v1/superadmin/logs/stream?token=nonsense").status_code, 401
        )

    def test_the_panel_reads_a_month_and_its_containers(self):
        headers = {"Authorization": f"Bearer {self._token()}"}

        months = self.client.get("/api/v1/superadmin/logs/months", headers=headers).json()
        self.assertEqual(months["current"], log_archive.month_key())
        self.assertIn(log_archive.month_key(), [item["month"] for item in months["months"]])

        page = self.client.get("/api/v1/superadmin/logs?limit=10", headers=headers).json()
        self.assertEqual([line["m"] for line in page["lines"]], ["salom"])
        self.assertGreater(page["offset"], 0)

    def test_a_malformed_month_is_refused(self):
        headers = {"Authorization": f"Bearer {self._token()}"}

        response = self.client.get("/api/v1/superadmin/logs?month=avgust", headers=headers)

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
