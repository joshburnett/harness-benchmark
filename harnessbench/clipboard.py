"""Copy text to the system clipboard with the standard library only.

Tries the native method for the host OS first, then command-line tools, then the
OSC 52 terminal escape sequence (which only works in terminals that support it).
"""

import base64
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from harnessbench import runs

WSL_CLIP = Path("/mnt/c/Windows/System32/clip.exe")


def copy(text: str) -> str:
    """Copy text and return the name of the method that worked; raise OSError if none did."""
    errors = []
    for name, method in methods():
        try:
            method(text)
            return name
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{name}: {exc}")
    raise OSError("; ".join(errors) or "no clipboard method available")


def methods() -> list[tuple[str, Callable[[str], None]]]:
    found: list[tuple[str, Callable[[str], None]]] = []
    if sys.platform == "win32":
        found.append(("Windows clipboard API", _windows_api))
        found.append(("clip.exe", lambda text: _clip_exe("clip.exe", text)))
    elif sys.platform == "darwin":
        found.append(
            (
                "pbcopy",
                lambda text: _run(["pbcopy"], text.encode(), {"LANG": "en_US.UTF-8"}),
            )
        )
    else:
        if runs.is_wsl() and (
            clip := shutil.which("clip.exe")
            or (str(WSL_CLIP) if WSL_CLIP.exists() else None)
        ):
            found.append(
                ("clip.exe (Windows clipboard)", lambda text: _clip_exe(clip, text))
            )
        if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
            found.append(("wl-copy", lambda text: _run(["wl-copy"], text.encode())))
        if os.environ.get("DISPLAY"):
            if shutil.which("xclip"):
                found.append(
                    (
                        "xclip",
                        lambda text: _run(
                            ["xclip", "-selection", "clipboard"], text.encode()
                        ),
                    )
                )
            if shutil.which("xsel"):
                found.append(
                    (
                        "xsel",
                        lambda text: _run(
                            ["xsel", "--clipboard", "--input"], text.encode()
                        ),
                    )
                )
    if sys.stdout.isatty():
        found.append(
            ("OSC 52 terminal escape (works only if your terminal supports it)", _osc52)
        )
    return found


def _run(argv: list[str], data: bytes, extra_env: dict[str, str] | None = None) -> None:
    # xclip and wl-copy fork a process that keeps serving the clipboard; it must not
    # inherit our stdout/stderr pipes or this call would wait for it to exit.
    env = os.environ | (extra_env or {})
    subprocess.run(
        argv,
        input=data,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        timeout=10,
        check=True,
    )


def _clip_exe(path: str, text: str) -> None:
    # clip.exe reads stdin in the console code page unless it starts with a UTF-16 BOM.
    data = "\ufeff".encode("utf-16-le") + text.replace("\n", "\r\n").encode("utf-16-le")
    _run([path], data)


def _osc52(text: str) -> None:
    encoded = base64.b64encode(text.encode()).decode()
    sys.stdout.write(f"\033]52;c;{encoded}\a")
    sys.stdout.flush()


def _windows_api(text: str) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]

    data = text.replace("\n", "\r\n").encode("utf-16-le") + b"\0\0"
    # Another application may briefly hold the clipboard open.
    for _ in range(20):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        pointer = kernel32.GlobalLock(handle)
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(13, handle):  # CF_UNICODETEXT
            kernel32.GlobalFree(handle)
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        user32.CloseClipboard()
