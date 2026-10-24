"""Direct LLM API latency, no harness: time-to-headers, time-to-first-token, decode rate.

Gives the provider-side baseline to subtract from the in-harness turn gaps. Also detects
proxy buffering: if the whole response arrives in one burst (stream_span ~ 0 ms for a
long answer), something between you and the provider is buffering the stream.

    python llm_probe.py --provider anthropic --model claude-sonnet-4-5
    python llm_probe.py --provider openai --model gpt-5 --pad-tokens 20000
    python llm_probe.py --provider openai --base-url https://openrouter.ai/api/v1 \\
        --api-key-env OPENROUTER_API_KEY --model anthropic/claude-sonnet-4.5
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator

from harnessbench import runs, stats

PROVIDERS = {
    "anthropic": ("https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
}
TASK = "Count from 1 to 80, separated by single spaces. Output nothing else."
FILLER = (
    "The quick brown fox jumps over the lazy dog while the benchmark measures prefill. "
)


def build_prompt(pad_tokens: int, cache_bust: bool) -> str:
    nonce = f"[request {uuid.uuid4().hex}]\n" if cache_bust else ""
    padding = FILLER * (pad_tokens // 17) if pad_tokens else ""
    return f"{nonce}{padding}\n{TASK}"


def build_request(args: argparse.Namespace, prompt: str) -> urllib.request.Request:
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise SystemExit(f"set {args.api_key_env}")
    messages = [{"role": "user", "content": prompt}]
    if args.provider == "anthropic":
        url = f"{args.base_url}/messages"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        body = {
            "model": args.model,
            "max_tokens": args.max_tokens,
            "stream": True,
            "messages": messages,
        }
    else:
        url = f"{args.base_url}/chat/completions"
        headers = {"authorization": f"Bearer {api_key}"}
        body = {
            "model": args.model,
            "max_completion_tokens": args.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": messages,
        }
    headers["content-type"] = "application/json"
    return urllib.request.Request(
        url, json.dumps(body).encode(), headers, method="POST"
    )


def sse_events(response) -> Iterator[dict]:
    for raw in response:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue


def event_text_and_usage(provider: str, event: dict) -> tuple[str, dict]:
    if provider == "anthropic":
        match event.get("type"):
            case "content_block_delta":
                return event.get("delta", {}).get("text", ""), {}
            case "message_start":
                return "", event.get("message", {}).get("usage", {})
            case "message_delta":
                return "", event.get("usage", {})
        return "", {}
    choices = event.get("choices") or [{}]
    text = (choices[0].get("delta") or {}).get("content") or ""
    return text, event.get("usage") or {}


def call_once(args: argparse.Namespace) -> dict:
    record: dict = {
        "kind": "llm",
        "env": args.env,
        "tag": args.tag,
        "provider": args.provider,
        "model": args.model,
    }
    request = build_request(args, build_prompt(args.pad_tokens, not args.allow_cache))
    usage: dict = {}
    chunk_times: list[int] = []
    start = time.perf_counter_ns()
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            record["headers_ms"] = (time.perf_counter_ns() - start) / 1e6
            for event in sse_events(response):
                text, event_usage = event_text_and_usage(args.provider, event)
                usage |= event_usage
                if text:
                    chunk_times.append(time.perf_counter_ns())
    except urllib.error.HTTPError as exc:
        record["error"] = (
            f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}"
        )
        return record
    except (urllib.error.URLError, OSError) as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return record
    record["total_ms"] = (time.perf_counter_ns() - start) / 1e6
    record["chunks"] = len(chunk_times)
    record["input_tokens"] = usage.get("input_tokens") or usage.get("prompt_tokens")
    record["output_tokens"] = usage.get("output_tokens") or usage.get(
        "completion_tokens"
    )
    if chunk_times:
        record["ttft_ms"] = (chunk_times[0] - start) / 1e6
        record["stream_span_ms"] = (chunk_times[-1] - chunk_times[0]) / 1e6
        if record["output_tokens"] and record["stream_span_ms"] > 0:
            record["tok_per_s"] = record["output_tokens"] / (
                record["stream_span_ms"] / 1000
            )
    return record


def print_summary(records: list[dict]) -> None:
    ok = [r for r in records if not r.get("error")]
    rows = [
        [
            name,
            stats.fmt(stats.median(r.get(key) for r in ok), digits),
            stats.fmt(stats.summarize(r.get(key) for r in ok)["p90"], digits),
        ]
        for name, key, digits in [
            ("headers_ms", "headers_ms", 0),
            ("ttft_ms", "ttft_ms", 0),
            ("stream_span_ms", "stream_span_ms", 0),
            ("total_ms", "total_ms", 0),
            ("tok_per_s", "tok_per_s", 1),
            ("chunks", "chunks", 0),
            ("input_tokens", "input_tokens", 0),
            ("output_tokens", "output_tokens", 0),
        ]
    ]
    print(stats.render_table(["metric", "median", "p90"], rows))
    for record in records:
        if record.get("error"):
            print("error:", record["error"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--provider", choices=tuple(PROVIDERS), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--base-url", help="override, e.g. OpenRouter or a local server"
    )
    parser.add_argument("--api-key-env", help="env var holding the API key")
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=400)
    parser.add_argument(
        "--pad-tokens", type=int, default=0, help="approx extra prompt tokens"
    )
    parser.add_argument(
        "--allow-cache", action="store_true", help="omit the per-request nonce"
    )
    parser.add_argument(
        "--pause", type=float, default=2.0, help="seconds between requests"
    )
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--env", default=runs.default_env())
    parser.add_argument("--tag", default="manual")
    args = parser.parse_args()
    default_url, default_key = PROVIDERS[args.provider]
    args.base_url = (args.base_url or default_url).rstrip("/")
    args.api_key_env = args.api_key_env or default_key
    return args


def main() -> int:
    args = parse_args()
    out = runs.kind_file("llm", args.env, f"{args.tag}-{args.model}")
    runs.append_jsonl(
        out,
        {"kind": "meta", "env": args.env, "tag": args.tag, "host": runs.host_info()},
    )
    records = []
    for rep in range(args.reps):
        if rep:
            time.sleep(args.pause)
        record = call_once(args)
        records.append(record)
        runs.append_jsonl(out, record)
        print(
            f"rep {rep + 1}: ttft={stats.fmt(record.get('ttft_ms'))} ms total={stats.fmt(record.get('total_ms'))} ms"
        )
    print(f"writing {out}")
    print_summary(records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
