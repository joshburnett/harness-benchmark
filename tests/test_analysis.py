from harnessbench import analysis, procinfo, timing

MS = 1_000_000


def probe(
    seq: int, shell_create: int, ready: int, py_create: int, t0: int, end: int
) -> dict:
    return {
        "seq": seq,
        "shell_ready_ns": ready,
        "py_t0_ns": t0,
        "probe_end_ns": end,
        "chain": [
            {"pid": 3, "name": "python.exe", "create_ns": py_create},
            {"pid": 2, "name": "powershell.exe", "create_ns": shell_create},
            {"pid": 1, "name": "Cursor.exe", "create_ns": 0},
        ],
    }


def test_dotnet_ticks_round_trip():
    assert timing.dotnet_ticks_to_ns(timing.DOTNET_UNIX_EPOCH_TICKS + 15) == 1500


def test_epoch_text_handles_comma_locale_and_garbage():
    assert timing.epoch_text_to_ns("1700000000,5") == 1_700_000_000_500_000_000
    assert timing.epoch_text_to_ns("") is None
    assert timing.epoch_text_to_ns("abc") is None


def test_find_shell_skips_self_and_launchers():
    chain = [
        {"name": "python.exe"},
        {"name": "python.exe"},
        {"name": "-zsh"},
        {"name": "Cursor"},
    ]
    assert procinfo.find_shell(chain) == 2


def test_alternate_mode_components():
    markers = {1: 1000 * MS, 2: 6000 * MS}
    probes = [probe(1, 3000 * MS, 4500 * MS, 4600 * MS, 4700 * MS, 4800 * MS)]
    row = analysis.call_rows(probes, markers, "alternate")[0]
    assert row["pre_create_ms"] == 2000
    assert row["shell_init_ms"] == 1500
    assert row["exec_launch_ms"] == 100
    assert row["py_init_ms"] == 100
    assert row["turn_to_shell_ms"] == 3500
    assert row["turn_to_native_ms"] == 1200
    assert row["reused_shell"] is False


def test_reused_shell_is_flagged_and_not_counted_as_startup():
    markers = {1: 5000 * MS}
    probes = [probe(1, 100 * MS, 6000 * MS, 6010 * MS, 6050 * MS, 6060 * MS)]
    row = analysis.call_rows(probes, markers, "alternate")[0]
    assert row["reused_shell"] is True
    assert row["shell_init_ms"] is None
    assert row["turn_to_shell_ms"] == 1000


def test_live_ancestry_has_creation_times():
    chain = procinfo.ancestors()
    assert chain[0]["create_ns"] is not None
    assert all(
        a["create_ns"] <= b["create_ns"] for a, b in zip(chain[1:], chain, strict=False)
    )
