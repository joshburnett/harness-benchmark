import pytest

from harnessbench import clipboard


def failing(text: str) -> None:
    raise OSError("not available")


def test_falls_through_to_first_working_method(monkeypatch):
    copied = []
    monkeypatch.setattr(
        clipboard,
        "methods",
        lambda: [
            ("broken", failing),
            ("works", copied.append),
            ("unused", copied.append),
        ],
    )
    assert clipboard.copy("hello") == "works"
    assert copied == ["hello"]


def test_reports_every_failure_when_nothing_works(monkeypatch):
    monkeypatch.setattr(clipboard, "methods", lambda: [("a", failing), ("b", failing)])
    with pytest.raises(OSError, match="a: not available; b: not available"):
        clipboard.copy("hello")


def test_platform_has_a_native_method():
    names = [name for name, _ in clipboard.methods()]
    assert names, "expected at least one clipboard method on this platform"
