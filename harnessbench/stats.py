"""Small summary-statistics and table helpers."""

import statistics
from collections.abc import Callable, Iterable


def percentile(sorted_values: list[float], q: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = q * (len(sorted_values) - 1)
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (
        position - low
    )


def summarize(values: Iterable[float | None]) -> dict:
    present = sorted(v for v in values if v is not None)
    if not present:
        return {"n": 0, "median": None, "p90": None, "max": None}
    return {
        "n": len(present),
        "median": statistics.median(present),
        "p90": percentile(present, 0.9),
        "max": present[-1],
    }


def median(values: Iterable[float | None]) -> float | None:
    return summarize(values)["median"]


def group_by(
    records: Iterable[dict], key: Callable[[dict], tuple]
) -> dict[tuple, list[dict]]:
    groups: dict[tuple, list[dict]] = {}
    for record in records:
        groups.setdefault(key(record), []).append(record)
    return groups


def fmt(value: float | int | str | bool | None, digits: int = 0) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool) or isinstance(value, str):
        return str(value)
    return f"{value:,.{digits}f}"


def render_table(headers: list[str], rows: list[list[str]], text_cols: int = 1) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        widths = [max(w, len(cell)) for w, cell in zip(widths, row, strict=True)]

    def line(cells: list[str]) -> str:
        parts = [
            cell.ljust(width) if index < text_cols else cell.rjust(width)
            for index, (cell, width) in enumerate(zip(cells, widths, strict=True))
        ]
        return "  ".join(parts).rstrip()

    rule = "  ".join("-" * w for w in widths)
    return "\n".join([line(headers), rule, *(line(row) for row in rows)])
