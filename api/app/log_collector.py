"""Copies every container's output into the monthly archive.

Runs as its own small service. It asks Docker for each running container's
output through the same socket ``docker compose logs`` uses, and appends what
comes back to the current month's file. Nothing is read from the containers
themselves, so a service that is restarting, rebuilt or scaled keeps its
history rather than starting from nothing.

Polling rather than one long follow-stream per container: a poll carries its
own resume point (``since``), so a collector that is restarted mid-deploy picks
up exactly where it stopped instead of leaving a hole or repeating a minute.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import signal
import sys
import time

import httpx

from app.config import get_settings
from app.log_archive import archive_dir, compress_finished_months, encode_line, month_key


# Docker's stream frames: one byte for the stream, three padding, four for the
# payload length. Only sent when the container has no TTY, which is the case
# for everything in this compose file.
_FRAME_HEADER = 8
_STREAM_NAMES = {1: "stdout", 2: "stderr"}
_STATE_NAME = ".collector-state.json"


def _docker_client(settings) -> httpx.Client:
    transport = httpx.HTTPTransport(uds=settings.docker_socket_path)
    return httpx.Client(transport=transport, base_url="http://docker", timeout=30.0)


def _state_path() -> Path:
    return archive_dir() / _STATE_NAME


def _load_state() -> dict:
    try:
        with _state_path().open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_state(state: dict) -> None:
    path = _state_path()
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(state, handle)
        temporary.replace(path)
    except OSError:
        pass


def demultiplex(payload: bytes) -> list[tuple[str, str]]:
    """Split Docker's framed stream into (stream name, text) pairs."""
    output: list[tuple[str, str]] = []
    position = 0
    total = len(payload)
    while position + _FRAME_HEADER <= total:
        header = payload[position : position + _FRAME_HEADER]
        stream = _STREAM_NAMES.get(header[0])
        size = int.from_bytes(header[4:8], "big")
        if stream is None or size <= 0 or position + _FRAME_HEADER + size > total:
            # Not a frame header after all (a container with a TTY, or a short
            # read). Treat the remainder as plain text rather than dropping it.
            text = payload[position:].decode("utf-8", errors="replace")
            if text.strip():
                output.append(("stdout", text))
            return output
        chunk = payload[position + _FRAME_HEADER : position + _FRAME_HEADER + size]
        output.append((stream, chunk.decode("utf-8", errors="replace")))
        position += _FRAME_HEADER + size
    return output


def split_timestamp(line: str) -> tuple[str, str]:
    """Docker prefixes each line with an RFC3339 timestamp when asked to."""
    head, separator, tail = line.partition(" ")
    if separator and len(head) >= 20 and head[4] == "-" and head[10] == "T":
        return head, tail
    return "", line


def _normalise(moment: str) -> str:
    """Docker's nanosecond timestamps, shortened to something JS can parse."""
    if not moment:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    text = moment.replace("Z", "+00:00")
    if "." in text:
        head, _, tail = text.partition(".")
        digits = "".join(character for character in tail if character.isdigit())[:3]
        offset = tail[len(digits) :].lstrip("0123456789")
        text = f"{head}.{digits or '000'}{offset}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Collector:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.state = _load_state()
        self.running = True
        self._last_month = month_key()

    def stop(self, *_args) -> None:
        self.running = False

    # -- docker ----------------------------------------------------------
    def containers(self, client: httpx.Client) -> list[dict]:
        response = client.get("/containers/json", params={"all": "0"})
        response.raise_for_status()
        found = []
        for item in response.json():
            names = item.get("Names") or []
            name = (names[0] if names else item.get("Id", ""))[:64].lstrip("/")
            found.append({"id": item.get("Id"), "name": name or "unknown"})
        return found

    def fetch(self, client: httpx.Client, container: dict) -> list[tuple[str, str, str]]:
        since = self.state.get(container["id"], {}).get("since")
        params = {"stdout": "1", "stderr": "1", "timestamps": "1", "tail": "0" if since is None else "all"}
        if since:
            params["since"] = since
        else:
            # First sight of a container: take the recent past so the panel is
            # not empty right after a deploy, but not its whole history.
            params["tail"] = str(self.settings.log_initial_lines)
        response = client.get(f"/containers/{container['id']}/logs", params=params)
        response.raise_for_status()
        lines: list[tuple[str, str, str]] = []
        for stream, text in demultiplex(response.content):
            for raw in text.split("\n"):
                if not raw.strip():
                    continue
                moment, message = split_timestamp(raw)
                lines.append((_normalise(moment), stream, message))
        return lines

    # -- archive ---------------------------------------------------------
    def append(self, entries: list[tuple[str, str, str, str]]) -> None:
        if not entries:
            return
        directory = archive_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{month_key()}.log"
        with path.open("a", encoding="utf-8") as handle:
            for moment, container, stream, message in entries:
                handle.write(encode_line(moment, container, stream, message) + "\n")

    def turn(self, client: httpx.Client) -> int:
        batch: list[tuple[str, str, str, str]] = []
        for container in self.containers(client):
            try:
                lines = self.fetch(client, container)
            except (httpx.HTTPError, ValueError):
                continue
            if not lines:
                continue
            seen = self.state.setdefault(container["id"], {})
            newest = seen.get("newest") or ""
            fresh = [item for item in lines if item[0] > newest]
            if not fresh:
                continue
            for moment, stream, message in fresh:
                batch.append((moment, container["name"], stream, message))
            seen["newest"] = fresh[-1][0]
            # Ask Docker for everything strictly after the last line we kept.
            # A whole second of overlap is re-read and then discarded above,
            # which is what keeps a restart from losing lines.
            seen["since"] = str(int(datetime.fromisoformat(
                fresh[-1][0].replace("Z", "+00:00")
            ).timestamp()))
            seen["name"] = container["name"]
        batch.sort(key=lambda item: item[0])
        self.append(batch)
        if batch:
            _save_state(self.state)
        return len(batch)

    def run(self) -> None:
        settings = self.settings
        archive_dir().mkdir(parents=True, exist_ok=True)
        compress_finished_months()
        interval = max(1, int(settings.log_poll_seconds))
        with _docker_client(settings) as client:
            while self.running:
                try:
                    self.turn(client)
                except httpx.HTTPError as error:
                    print(f"[log-collector] docker bilan aloqa yo'q: {error}", flush=True)
                    time.sleep(min(30, interval * 5))
                    continue
                except Exception as error:  # keep the service alive
                    print(f"[log-collector] xato: {type(error).__name__}: {error}", flush=True)
                current = month_key()
                if current != self._last_month:
                    # A new month started: last month's file can no longer grow.
                    compress_finished_months()
                    self._last_month = current
                time.sleep(interval)


def main() -> int:
    collector = Collector()
    signal.signal(signal.SIGTERM, collector.stop)
    signal.signal(signal.SIGINT, collector.stop)
    print("[log-collector] ishga tushdi", flush=True)
    collector.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
