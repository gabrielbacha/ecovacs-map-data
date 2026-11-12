"""Pure geometry helpers for Ecovacs map data."""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

_NUMBER_PATTERN = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass(frozen=True, slots=True)
class TracePoint:
    """A decoded Ecovacs cleaning trace point."""

    x: int
    y: int
    connected: bool


class TraceAccumulator:
    """Collect bounded trace chunks and cache one contiguous generation."""

    MAX_POINTS = 1_000_000
    MAX_CHUNK_POINTS = 50_000
    MAX_CHUNKS = 4096

    def __init__(self) -> None:
        self._chunks: dict[int, tuple[TracePoint, ...]] = {}
        self._total = 0
        self._cached_points: tuple[TracePoint, ...] | None = None
        self._cached_path: str | None = None

    @property
    def total(self) -> int:
        return self._total

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def _assemble(self) -> tuple[TracePoint, ...]:
        if self._cached_points is None:
            points: list[TracePoint] = []
            expected_start = 0
            for start, chunk in sorted(self._chunks.items()):
                if start != expected_start:
                    break
                points.extend(chunk)
                expected_start += len(chunk)
            self._cached_points = tuple(points)
            self._cached_path = trace_points_to_svg_path(self._cached_points)
        return self._cached_points

    @property
    def points(self) -> list[TracePoint]:
        """Return a defensive copy of the contiguous trace prefix."""
        return list(self._assemble())

    @property
    def point_count(self) -> int:
        return len(self._assemble())

    @property
    def svg_path(self) -> str | None:
        self._assemble()
        return self._cached_path

    @property
    def complete(self) -> bool:
        return self.point_count == self._total

    def update(self, start: int, total: int, points: Iterable[TracePoint]) -> None:
        """Validate the whole update before replacing retained state."""
        if type(start) is not int or type(total) is not int:
            raise ValueError("Trace offsets and totals must be integers")
        if not 0 <= start <= total <= self.MAX_POINTS:
            raise ValueError("Trace offsets or total exceed the point bounds")
        chunk_list: list[TracePoint] = []
        for point in points:
            if len(chunk_list) >= min(self.MAX_CHUNK_POINTS, total - start):
                raise ValueError("Trace chunk exceeds the point bounds")
            if not isinstance(point, TracePoint):
                raise TypeError("Invalid trace point")
            chunk_list.append(point)
        chunk = tuple(chunk_list)
        candidate = {} if start == 0 else dict(self._chunks)
        # A shorter response drops old tails and clips its final retained chunk.
        candidate = {
            offset: items[: total - offset]
            for offset, items in candidate.items()
            if offset < total
        }
        if chunk:
            candidate[start] = chunk
        else:
            candidate.pop(start, None)
        if len(candidate) > self.MAX_CHUNKS:
            raise ValueError("Trace exceeds the chunk count bound")
        end = -1
        for offset, items in sorted(candidate.items()):
            if offset < end:
                raise ValueError("Trace chunks must not overlap")
            end = offset + len(items)
        if candidate == self._chunks and total == self._total:
            return
        self._chunks = candidate
        self._total = total
        self._cached_points = None
        self._cached_path = None


def decode_trace_chunk(
    value: str, decompressor: Callable[[str], bytes]
) -> list[TracePoint]:
    """Decode an Ecovacs base64-compressed trace chunk."""
    if not value.strip():
        return []

    if len(value) > 1_000_000:
        raise ValueError("Encoded trace chunk exceeds the byte bound")
    raw = decompressor(value)
    if len(raw) > TraceAccumulator.MAX_CHUNK_POINTS * 5:
        raise ValueError("Decoded trace chunk exceeds the point bound")
    if len(raw) % 5:
        raise ValueError("Invalid trace points length")

    return [
        TracePoint(
            x=int.from_bytes(raw[index : index + 2], "little", signed=True),
            y=int.from_bytes(raw[index + 2 : index + 4], "little", signed=True),
            connected=(raw[index + 4] & 0x80) == 0,
        )
        for index in range(0, len(raw), 5)
    ]


def trace_points_to_svg_path(points: Iterable[TracePoint]) -> str | None:
    """Build a compact relative SVG path from decoded trace points."""
    trace = list(points)
    if len(trace) < 2:
        return None

    path = [f"M{trace[0].x} {trace[0].y}"]
    previous = trace[0]
    for point in trace[1:]:
        x = point.x - previous.x
        y = point.y - previous.y
        previous = point
        if x == 0 and y == 0:
            continue
        if not point.connected:
            path.append(f"m{x} {y}")
        elif x == 0:
            path.append(f"v{y}")
        elif y == 0:
            path.append(f"h{x}")
        else:
            path.append(f"l{x} {y}")
    return "".join(path)


def normalize_position_type(value: Any) -> str | None:
    """Normalize deebot-client position enum variants for consumers."""
    name = getattr(value, "name", str(value)).lower()
    if "charger" in name or "station" in name or "dock" in name:
        return "charger"
    if "deebot" in name or "vacuum" in name or "robot" in name:
        return "deebot"
    return None


def parse_coordinates(value: str) -> list[list[float]]:
    """Parse an Ecovacs coordinate string into x/y pairs."""
    numbers = [float(item) for item in _NUMBER_PATTERN.findall(value)]
    return [numbers[index : index + 2] for index in range(0, len(numbers) - 1, 2)]


def rotation_degrees(angle: Any) -> int:
    """Convert deebot-client's RotationAngle to degrees."""
    name = getattr(angle, "name", str(angle)).upper()
    match = re.search(r"(?:DEG_)?(0|90|180|270)$", name)
    if match:
        return int(match.group(1))
    return 0
