"""Process ancestry with OS-reported creation times, using only ctypes and /proc.

The creation time comes from the kernel, so it marks the moment the harness's
shell process came into existence, before any shell startup, profile loading,
or security-software inspection that happens inside the new process.
"""

import ctypes
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from harnessbench import timing

SHELL_NAMES = {"bash", "zsh", "sh", "dash", "fish", "pwsh", "powershell", "cmd", "nu"}


def ancestors(max_depth: int = 8) -> list[dict]:
    """Return [this process, parent, grandparent, ...] with pid, ppid, name, create_ns."""
    try:
        if sys.platform == "win32":
            return _windows_ancestors(max_depth)
        if sys.platform == "darwin":
            return _walk(_mac_info, os.getpid(), max_depth)
        if sys.platform.startswith("linux"):
            return _walk(_linux_info, os.getpid(), max_depth)
    except OSError as exc:
        return [{"pid": os.getpid(), "error": f"{type(exc).__name__}: {exc}"}]
    return [{"pid": os.getpid(), "ppid": os.getppid(), "name": None, "create_ns": None}]


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    base = name.replace("\\", "/").rsplit("/", 1)[-1].lower().lstrip("-")
    return base.removesuffix(".exe")


def find_shell(chain: list[dict]) -> int | None:
    """Index of the nearest ancestor (excluding the process itself) that is a shell."""
    for index, proc in enumerate(chain[1:], start=1):
        if normalize_name(proc.get("name")) in SHELL_NAMES:
            return index
    return None


def _walk(
    info_fn: Callable[[int], dict | None], pid: int, max_depth: int
) -> list[dict]:
    chain: list[dict] = []
    while pid > 0 and len(chain) < max_depth:
        info = info_fn(pid)
        if info is None:
            break
        chain.append(info)
        if info["ppid"] == pid:
            break
        pid = info["ppid"]
    return chain


def _linux_info(pid: int) -> dict | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        raw_cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    # comm can contain spaces and parentheses, so split after the last ')'.
    rparen = stat.rindex(")")
    name = stat[stat.index("(") + 1 : rparen]
    fields = stat[rparen + 2 :].split()
    ppid = int(fields[1])
    start_ticks = int(fields[19])
    boot_epoch_ns = time.time_ns() - time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    create_ns = boot_epoch_ns + start_ticks * 1_000_000_000 // os.sysconf("SC_CLK_TCK")
    cmdline = raw_cmdline.replace(b"\0", b" ").decode(errors="replace").strip()
    return {
        "pid": pid,
        "ppid": ppid,
        "name": name,
        "create_ns": create_ns,
        "cmdline": cmdline[:300],
    }


class _ProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


_PROC_PIDTBSDINFO = 3
_libsystem: ctypes.CDLL | None = None


def _mac_info(pid: int) -> dict | None:
    global _libsystem
    if _libsystem is None:
        _libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        _libsystem.proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        _libsystem.proc_pidinfo.restype = ctypes.c_int
    info = _ProcBsdInfo()
    size = ctypes.sizeof(info)
    if (
        _libsystem.proc_pidinfo(pid, _PROC_PIDTBSDINFO, 0, ctypes.byref(info), size)
        != size
    ):
        return None
    name = (info.pbi_name or info.pbi_comm).decode(errors="replace")
    create_ns = info.pbi_start_tvsec * 1_000_000_000 + info.pbi_start_tvusec * 1_000
    return {"pid": pid, "ppid": info.pbi_ppid, "name": name, "create_ns": create_ns}


def _windows_ancestors(max_depth: int) -> list[dict]:
    from ctypes import wintypes

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    entry_ptr = ctypes.POINTER(ProcessEntry32W)
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, entry_ptr]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, entry_ptr]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    filetime_ptr = ctypes.POINTER(wintypes.FILETIME)
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [filetime_ptr] * 4
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    table: dict[int, tuple[int, str]] = {}
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(ProcessEntry32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            table[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)

    def create_ns(pid: int) -> int | None:
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return None
            created = times[0]
            return timing.filetime_to_ns(
                (created.dwHighDateTime << 32) | created.dwLowDateTime
            )
        finally:
            kernel32.CloseHandle(handle)

    chain: list[dict] = []
    pid = os.getpid()
    while pid in table and len(chain) < max_depth:
        ppid, name = table[pid]
        created = create_ns(pid)
        child_created = chain[-1]["create_ns"] if chain else None
        # Windows reuses PIDs: a "parent" created after its child is an unrelated process.
        if created and child_created and created > child_created:
            break
        chain.append({"pid": pid, "ppid": ppid, "name": name, "create_ns": created})
        if ppid in (0, pid):
            break
        pid = ppid
    return chain
