"""Where the containers' output is kept, and how it is read back.

Docker itself only holds the last few megabytes per container and throws the
rest away, so "what happened last Tuesday" was a question nobody on this server
could answer. Every line the collector picks up is appended here instead, to
one file per calendar month.

One file per month is the whole retention policy: a new month starts a new
file, so the live view is never a year long, and the months that came before
stay on disk - compressed once they can no longer grow - for anyone scrolling
back. Nothing is deleted unless ``log_retention_months`` says how many to keep.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import gzip
import json
import os
import re

from app.config import get_settings


# Read this much at a time when walking a file backwards for the newest lines.
_TAIL_BLOCK_BYTES = 64 * 1024
# A single log line longer than this is cut: one runaway traceback must not be
# able to fill the disk or freeze the panel.
MAX_MESSAGE_CHARS = 8_000
_MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")


def archive_dir() -> Path:
    return Path(get_settings().log_archive_dir)


def month_key(moment: datetime | None = None) -> str:
    moment = moment or datetime.now(timezone.utc)
    return f"{moment.year:04d}-{moment.month:02d}"


def is_month_key(value: str) -> bool:
    return bool(_MONTH_PATTERN.match(str(value or "")))


def month_path(month: str, *, compressed: bool = False) -> Path:
    suffix = ".log.gz" if compressed else ".log"
    return archive_dir() / f"{month}{suffix}"


def existing_month_path(month: str) -> Path | None:
    """The file holding that month, plain or compressed, if we still have it."""
    if not is_month_key(month):
        return None
    plain = month_path(month)
    if plain.exists():
        return plain
    packed = month_path(month, compressed=True)
    return packed if packed.exists() else None


def encode_line(moment: str, container: str, stream: str, message: str) -> str:
    """One log line as it is stored: compact JSON, one per file line.

    JSON rather than a delimiter-separated format because a log message is
    arbitrary text - it contains tabs, quotes and newlines, and a viewer that
    guesses wrong about those shows a mangled stack trace exactly when someone
    is trying to read one.
    """
    text = (message or "").replace("\r", "")
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[:MAX_MESSAGE_CHARS] + " ...[qisqartirildi]"
    return json.dumps(
        {"t": moment, "c": container, "s": stream, "m": text},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode_line(raw: str) -> dict | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        # A line written by an older format, or a half-written line caught mid
        # append. Showing it as plain text beats hiding it.
        return {"t": "", "c": "", "s": "stdout", "m": raw}
    if not isinstance(value, dict):
        return None
    return {
        "t": str(value.get("t") or ""),
        "c": str(value.get("c") or ""),
        "s": str(value.get("s") or "stdout"),
        "m": str(value.get("m") or ""),
    }


def list_months() -> list[dict]:
    """Every month we hold, newest first."""
    directory = archive_dir()
    if not directory.is_dir():
        return []
    months: dict[str, dict] = {}
    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        if name.endswith(".log.gz"):
            key, archived = name[: -len(".log.gz")], True
        elif name.endswith(".log"):
            key, archived = name[: -len(".log")], False
        else:
            continue
        if not is_month_key(key):
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            size = 0
        months[key] = {"month": key, "bytes": size, "archived": archived}
    return [months[key] for key in sorted(months, reverse=True)]


def _open_month(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def _matches(entry: dict, container: str | None, query: str | None) -> bool:
    if container and entry.get("c") != container:
        return False
    if query:
        needle = query.casefold()
        if needle not in (entry.get("m") or "").casefold():
            return False
    return True


def read_page(
    month: str,
    *,
    limit: int = 300,
    before: int | None = None,
    container: str | None = None,
    query: str | None = None,
) -> dict:
    """The newest ``limit`` lines of a month, or the ones before an offset.

    ``before`` is a byte offset returned by an earlier call. Scrolling up the
    panel walks backwards through the file with it rather than re-reading the
    whole month each time, so a month that has grown to hundreds of megabytes
    still opens instantly.
    """
    path = existing_month_path(month)
    if path is None:
        return {"month": month, "lines": [], "next_before": None, "has_more": False}
    limit = max(1, min(int(limit or 300), 2000))

    if path.suffix == ".gz":
        # A finished month never changes, so there is no cheap seek into it:
        # read it once and take the slice that was asked for.
        with _open_month(path) as handle:
            entries = [decode_line(line) for line in handle]
        entries = [item for item in entries if item and _matches(item, container, query)]
        end = len(entries) if before is None else max(0, min(int(before), len(entries)))
        start = max(0, end - limit)
        return {
            "month": month,
            "lines": entries[start:end],
            "next_before": start if start > 0 else None,
            "has_more": start > 0,
        }

    size = path.stat().st_size
    end = size if before is None else max(0, min(int(before), size))
    collected: list[dict] = []
    # Where the oldest line handed back starts. It is a line boundary, which is
    # what makes it safe to pass back as ``before``: reading a byte offset that
    # lands mid-line is how a scrollback ends up showing half a JSON record.
    oldest = end
    exhausted = True
    with path.open("rb") as handle:
        tail = b""
        position = end
        first_block = True
        while position > 0:
            step = min(_TAIL_BLOCK_BYTES, position)
            position -= step
            handle.seek(position)
            data = handle.read(step) + tail
            pieces = data.split(b"\n")
            # Absolute file offset of every piece in this block.
            starts = []
            cursor = position
            for piece in pieces:
                starts.append(cursor)
                cursor += len(piece) + 1
            # The first piece begins before this block unless we reached the
            # start of the file; it is completed by the next block back.
            tail = pieces[0]
            last = len(pieces) - 1
            if first_block and pieces[last]:
                # The file was cut mid-line (a crash while appending). Show the
                # finished lines rather than a broken one.
                last -= 1
            first_block = False
            for index in range(last, 0, -1):
                piece = pieces[index]
                if not piece.strip():
                    continue
                entry = decode_line(piece.decode("utf-8", errors="replace"))
                if entry and _matches(entry, container, query):
                    collected.append(entry)
                    oldest = starts[index]
                    if len(collected) >= limit:
                        exhausted = False
                        break
            if len(collected) >= limit:
                break
        if position == 0 and len(collected) < limit and tail.strip():
            entry = decode_line(tail.decode("utf-8", errors="replace"))
            if entry and _matches(entry, container, query):
                collected.append(entry)
                oldest = 0
    collected.reverse()
    has_more = bool(not exhausted and oldest > 0)
    return {
        "month": month,
        "lines": collected,
        "next_before": oldest if has_more else None,
        "has_more": has_more,
        "size": size,
    }


def current_size() -> int:
    path = month_path(month_key())
    try:
        return path.stat().st_size
    except OSError:
        return 0


def read_since(offset: int) -> tuple[list[dict], int]:
    """New lines appended to the current month since a byte offset.

    Used by the live stream. A short read that lands mid-line is left for the
    next poll instead of being shown as a broken entry.
    """
    path = month_path(month_key())
    try:
        size = path.stat().st_size
    except OSError:
        return [], 0
    start = max(0, min(int(offset or 0), size))
    if start >= size:
        return [], size
    with path.open("rb") as handle:
        handle.seek(start)
        block = handle.read(size - start)
    if not block.endswith(b"\n"):
        cut = block.rfind(b"\n")
        if cut < 0:
            return [], start
        block, size = block[: cut + 1], start + cut + 1
    entries = []
    for piece in block.split(b"\n"):
        entry = decode_line(piece.decode("utf-8", errors="replace"))
        if entry:
            entries.append(entry)
    return entries, size


def known_containers(sample_bytes: int = 512 * 1024) -> list[str]:
    """Container names seen recently, for the panel's filter."""
    path = month_path(month_key())
    names: list[str] = []
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - sample_bytes))
            block = handle.read()
    except OSError:
        return names
    for piece in block.split(b"\n")[1:]:
        entry = decode_line(piece.decode("utf-8", errors="replace"))
        name = (entry or {}).get("c")
        if name and name not in names:
            names.append(name)
    return sorted(names)


def compress_finished_months(now: datetime | None = None) -> list[str]:
    """Pack every month that can no longer grow, and honour retention."""
    directory = archive_dir()
    if not directory.is_dir():
        return []
    current = month_key(now)
    packed: list[str] = []
    for entry in sorted(directory.glob("*.log")):
        key = entry.name[: -len(".log")]
        if not is_month_key(key) or key >= current:
            continue
        target = month_path(key, compressed=True)
        try:
            with entry.open("rb") as source, gzip.open(target, "wb") as sink:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    sink.write(chunk)
            os.replace(target, target)
            entry.unlink()
            packed.append(key)
        except OSError:
            # Leave the plain file in place; it is still readable, and the next
            # pass will try again.
            continue
    _apply_retention(current)
    return packed


def _apply_retention(current: str) -> None:
    keep = int(get_settings().log_retention_months or 0)
    if keep <= 0:
        # 0 means "keep everything", which is the default: an archive that
        # silently deletes history is worse than one that needs a bigger disk.
        return
    months = [item["month"] for item in list_months()]
    for key in months[keep:]:
        if key == current:
            continue
        for candidate in (month_path(key), month_path(key, compressed=True)):
            try:
                candidate.unlink()
            except OSError:
                pass
