"""Shared host-side validation and line framing."""

BAUD = 115200
LIMITS = {"delay": (100, 1_000_000), "spr": (1, 10_000_000),
          "pause": (0, 60_000), "steps": (1, 10_000_000)}


def number(value: str, field: str) -> int:
    low, high = LIMITS[field]
    if not value.isascii() or not value.isdigit() or not low <= int(value) <= high:
        raise ValueError(f"{field}: enter a whole number from {low:,} to {high:,}.")
    return int(value)


def parse_state(line: str) -> dict[str, str] | None:
    if not line.startswith("STATE "):
        return None
    fields = dict(part.split("=", 1) for part in line.split()[1:] if "=" in part)
    required = {"mode", "dir", "remaining", "delay", "spr", "pause", "phase"}
    return fields if required <= fields.keys() else None


class LineBuffer:
    """Keep partial serial reads until a full newline arrives."""

    def __init__(self):
        self.pending = bytearray()

    def feed(self, data: bytes) -> list[str]:
        self.pending.extend(data)
        lines = []
        while b"\n" in self.pending:
            line, _, rest = self.pending.partition(b"\n")
            self.pending = bytearray(rest)
            if len(line) <= 512:
                lines.append(line.decode("ascii", errors="replace").strip())
        if len(self.pending) > 512:
            raise ValueError("Serial line exceeds protocol limit")
        return lines
