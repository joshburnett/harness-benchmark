from harnessbench import completion

MS = 1_000_000


def complete(seq: int, variant: str, start_ms: int, exit_ms: int) -> dict:
    return {
        "kind": "complete",
        "seq": seq,
        "variant": variant,
        "start_ns": start_ms * MS,
        "exit_ns": exit_ms * MS,
        "returncode": 0,
    }


def scenario() -> tuple[dict, list[dict], dict[int, int]]:
    meta = {
        "steps": [
            {"seq": 1, "variant": "line"},
            {"seq": 2, "variant": "bg-holds-output"},
            {"seq": 3, "variant": "sleep-10"},
        ]
    }
    records = [
        complete(1, "line", 1100, 1200),
        complete(2, "bg-holds-output", 3100, 3200),
        {"kind": "child_exit", "seq": 2, "exit_ns": 8200 * MS},
        complete(3, "sleep-10", 9100, 19100),
    ]
    markers = {1: 1000 * MS, 2: 3000 * MS, 3: 9000 * MS, 4: 12000 * MS}
    return meta, records, markers


def test_detect_and_excess_relative_to_baseline():
    rows = completion.step_rows(*scenario())
    assert [r["detect_ms"] for r in rows] == [1800, 5800, -7100]
    summary = {v["variant"]: v for v in completion.variant_summary(rows)}
    assert summary["line"]["excess_ms"] == 0
    assert summary["bg-holds-output"]["excess_ms"] == 4000
    assert rows[1]["after_child_ms"] == 800


def test_early_return_is_flagged_not_invalid():
    meta, records, markers = scenario()
    rows = completion.step_rows(meta, records, markers)
    assert rows[2]["early"] is True
    assert completion.validate(meta, records, markers, rows) == []


def test_killed_background_child_is_flagged():
    meta, records, markers = scenario()
    records = [r for r in records if r["kind"] != "child_exit"]
    rows = completion.step_rows(meta, records, markers)
    assert rows[1]["killed"] is True


def test_wrong_variant_and_missing_steps_are_invalid():
    meta, records, markers = scenario()
    records[0] = complete(1, "silent", 1100, 1200)
    del markers[4]
    rows = completion.step_rows(meta, records[:2], markers)
    assert completion.validate(meta, records[:2], markers, rows) == [
        "missing probes [3]",
        "missing markers [4]",
        "probe 1 ran variant silent, not line",
    ]
