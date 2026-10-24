"""Timestamp conversions. Every timestamp stored by the kit is Unix-epoch nanoseconds."""

DOTNET_UNIX_EPOCH_TICKS = 621_355_968_000_000_000
FILETIME_UNIX_EPOCH = 116_444_736_000_000_000


def dotnet_ticks_to_ns(ticks: int) -> int:
    """Convert PowerShell's [DateTime]::UtcNow.Ticks (100 ns since year 1) to epoch ns."""
    return (ticks - DOTNET_UNIX_EPOCH_TICKS) * 100


def filetime_to_ns(filetime: int) -> int:
    """Convert a Windows FILETIME (100 ns since 1601) to epoch ns."""
    return (filetime - FILETIME_UNIX_EPOCH) * 100


def epoch_text_to_ns(text: str | None) -> int | None:
    """Parse a shell $EPOCHREALTIME value; some locales use a comma separator."""
    if not text:
        return None
    whole, _, frac = text.strip().replace(",", ".").partition(".")
    if not whole.isdigit() or (frac and not frac.isdigit()):
        return None
    return int(whole) * 1_000_000_000 + int((frac + "000000000")[:9])


def diff_ms(later_ns: int | None, earlier_ns: int | None) -> float | None:
    if later_ns is None or earlier_ns is None:
        return None
    return (later_ns - earlier_ns) / 1e6
