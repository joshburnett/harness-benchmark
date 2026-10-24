# Harness benchmarks

Measure where the time goes in a coding agent's tool-calling loop, and compare it
across operating systems, environments, agent harnesses, and models.

## Why

Coding agents feel very different on different machines. A powerful corporate
Windows workstation with security monitoring can feel much slower than a personal
laptop. Possible causes:

- **Process startup:** security software (EDR / antivirus) can delay every new
  PowerShell, bash, or `python.exe` process. It may verify signatures, scan
  scripts (AMSI), or make network lookups, possibly with a cache that expires.
- **Shell startup:** profiles, rc files, and login shells add time to every call.
- **Harness overhead:** how each harness dispatches tools, starts shells, and
  notices that a command has finished.
- **Network path:** corporate proxies, TLS inspection, and loss of HTTP/2 can slow
  down or buffer the stream from the LLM provider or the harness backend.
- **Model / provider:** time to first token and generation speed.

This kit separates those components so each variable can be tested on its own:
the same harness and model across environments, the same environment and model
across harnesses, or the same environment and harness across models.

Everything uses only the Python standard library, so nothing needs to be
installed on locked-down machines.

## How it works

### Measuring tool-call dispatch without harness instrumentation

You can't see inside a harness, but every tool call leaves timestamps on the
machine's clock:

1. **Shell process created:** the kernel's record of when the harness's shell
   process was created. `probe.py` reads it for its whole chain of parent
   processes (Win32 `GetProcessTimes`, macOS `proc_pidinfo`, Linux `/proc`).
2. **Shell ready:** a timestamp the shell takes itself as the first thing in the
   command, using a builtin so no extra process is started
   (`[DateTime]::UtcNow.Ticks` in PowerShell, `$EPOCHREALTIME` in bash/zsh).
3. **Python created:** the kernel's record for the `python` process the command
   launches.
4. **Python running:** the first line of `probe.py`.

### Separating model time from shell time

The generated prompt makes the agent alternate between two kinds of tool call:

- **WRITE:** create a marker file with the harness's native file tool. No process
  is started, and the file's modification time records when it happened.
- **RUN:** run `probe.py` through the shell tool.

The marker text is padded to about the same length as the probe command, so both
steps cost the model a similar number of output tokens.

```text
marker i written ─(model + harness)─► shell created ─(shell startup)─► shell ready
    ─► python created ─► python first line ─► probe done
probe done ─(model + harness, no process started)─► marker i+1 written
```

### Metrics

| Metric | Meaning |
|---|---|
| `pre_create` | Marker i written to shell process created: the model turn plus harness dispatch |
| `shell_init` | Shell created to first command running: profile, rc files, security inspection |
| `exec_launch` | Shell ready to Python process created: launching an executable from the shell |
| `py_init` | Python created to its first line: loading the program and starting the interpreter |
| `turn_native` | Probe done to next marker written: the turn gap when the next tool starts no process |
| `turn_shell` | Marker written to shell ready: the turn gap when the next tool starts a shell |
| `shell_overhead` | Median `turn_shell` minus median `turn_native`: what a shell tool call costs, net of the model |

If a harness reuses one long-lived shell, the shell's creation time is earlier
than the previous step. Those calls are flagged `reused`, and `shell_init` isn't
reported for them.

## Scripts

| Script | Run by | Purpose |
|---|---|---|
| `make_prompt.py` | you | Creates a run folder and prints the prompt to paste into the harness |
| `probe.py` | the agent | Records the timestamps above for one shell tool call |
| `spawn_bench.py` | you (optionally the agent) | Times starting shells, Python, git, node, and new scripts |
| `net_probe.py` | you | Times DNS, TCP, proxy, TLS, and first byte; shows certificate issuer and HTTP/2 support |
| `llm_probe.py` | you | Calls the provider API directly: time to first token, tokens/s, stream buffering |
| `launch.py` | you (CLI harnesses only, optional) | Records exact submit and exit times around a CLI harness |
| `analyze.py` | you | Summarizes everything under `runs/` into comparison tables |

## Setup (each machine / environment)

1. Copy this folder to the machine.
   - In **WSL**, put it in the Linux filesystem (e.g. `~/code/...`), not under
     `/mnt/c`. Files there are accessed across the Windows/Linux boundary, which is
     slow, and their modification times come from Windows.
2. Create a virtual environment. On Windows, use **Python 3.13+**: older versions
   only timestamp to about 15 ms.

   ```bash
   uv venv                       # or: python -m venv .venv
   uv pip install -r requirements-development.piptools   # pytest, only needed for tests
   ```

3. Check that process lookup works on this platform:

   ```bash
   ./.venv/bin/python -m pytest          # Windows: .\.venv\Scripts\python.exe -m pytest
   ```

The examples below use `python` for the virtual environment's interpreter
(`./.venv/bin/python` on macOS/Linux/WSL, `.\.venv\Scripts\python.exe` on Windows).

## Running an agent benchmark

1. Generate a prompt:

   ```bash
   python make_prompt.py --harness cursor-gui --model sonnet-4.5 --env corp-win --shell powershell
   ```

   | Option | Default | Notes |
   |---|---|---|
   | `--harness` | required | Free text label: `cursor-gui`, `cursor-cli`, `cline`, `pi`, `opencode`, `claude-code` |
   | `--model` | required | Free text label for the model selected in the harness |
   | `--env` | hostname + OS | Use consistent labels: `corp-win`, `corp-wsl`, `corp-vm`, `mac` |
   | `--shell` | `powershell` on Windows, else `$SHELL` | One of `powershell`, `pwsh`, `gitbash`, `bash`, `zsh`. Must match the shell the harness runs commands in |
   | `--calls` | 12 | Number of shell probe calls |
   | `--mode` | `alternate` | `shell-only` skips the WRITE steps, so `turn_native` and `shell_overhead` aren't measured |
   | `--spawn-bench` | off | Ends the run with `spawn_bench.py --quick` from inside the harness |
   | `--python` | current interpreter | Interpreter the agent's commands use |
   | `--notes` | empty | Free text saved in `meta.json` |

   The prompt is printed and saved as `runs/<run-id>/prompt.txt`.

2. Prepare the harness:
   - Open **this folder** as the workspace / project.
   - Turn on **auto-approve** for terminal commands and file edits. Otherwise you
     measure your own click time.
   - Choose the model and a **fixed, low (or no) reasoning setting**. Reasoning
     tokens add to every turn.
   - Start a **new chat** for each run.
3. Paste the prompt and wait for the agent to reply `DONE`.
4. Check the result:

   ```bash
   python analyze.py --run <run-id>
   ```

   There should be one probe row per call. If marker files are missing or
   steps are out of order, the agent didn't follow the prompt; rerun it.

### CLI harnesses (optional exact timing)

`launch.py` runs a CLI harness on the saved prompt and records when it was
submitted, so the report also includes time to the first tool call. Put
`{prompt}` (prompt text) or `{prompt_file}` (path to `prompt.txt`) in the
command, or use `--stdin` to pipe the prompt in:

```bash
python launch.py --run <run-id> -- cursor-agent -p --force {prompt}
python launch.py --run <run-id> -- claude -p --dangerously-skip-permissions {prompt}
python launch.py --run <run-id> -- opencode run {prompt}
```

Check each CLI's own flags for non-interactive mode and auto-approval. Pasting the
prompt manually works too; you just don't get the submit-time measurement.

## Baseline benchmarks (run manually, no harness)

Run these from a plain terminal in each session, so agent runs can be compared
with the machine's baseline.

### Process spawn: `spawn_bench.py`

```bash
python spawn_bench.py --env corp-win                       # 5 reps, no idle
python spawn_bench.py --env corp-win --idle 0,10,60,300,900 --reps 3
python spawn_bench.py --env corp-win --only powershell python
```

Each command runs twice back to back: `first`, then `repeat`. Commands tested:

- **Windows:** cmd, `whoami.exe`, Windows PowerShell and pwsh (with and without
  profile), the same `.ps1` script vs a brand-new one each time, Git Bash (`-c`
  and login `-l -c`)
- **macOS / Linux:** `true`, `sh`, bash, zsh (including interactive `-i`, which
  loads rc files), a brand-new zsh script each time
- **Everywhere:** Python (`-I -S`, normal, same script vs new script), git, node

How to read the results:

- **`first` much slower than `repeat`, and worse after longer idles:** points to a
  per-executable check whose cached result expires. The idle run takes a while:
  the sum of the idle periods times `--reps`.
- **New script slower than the same script:** points to content scanning of new
  files (e.g. AMSI for PowerShell).
- **Profile vs no profile, `-i`/`-l` vs plain:** the cost of shell startup files,
  which interactive harness terminals pay on every call.
- **`--spawn-bench` in an agent run vs a manual run:** shows whether security
  software treats processes started by the harness (Electron/node) differently
  from ones you start yourself.

### Network path: `net_probe.py`

```bash
python net_probe.py --env corp-win
python net_probe.py --env corp-win --hosts api.anthropic.com api2.cursor.sh
```

Reports DNS, TCP, proxy CONNECT, TLS handshake, and time to first byte for each
host. It uses the `HTTPS_PROXY` environment variable or the system's proxy
setting; PAC files aren't supported.

- A corporate **issuer** (Zscaler, Netskope, Palo Alto, ...) instead of a public
  certificate authority means TLS inspection.
- **No `h2` in the alpn column** means HTTP/2 isn't available on that path.
- **`verified` = NO:** the certificate failed verification and the timing was
  retried without it. This often means a corporate root certificate that Python
  doesn't trust.

### Direct LLM API: `llm_probe.py`

Needs an API key in an environment variable.

```bash
python llm_probe.py --provider anthropic --model claude-sonnet-4-5
python llm_probe.py --provider openai --model gpt-5 --pad-tokens 20000
python llm_probe.py --provider openai --base-url https://openrouter.ai/api/v1 \
    --api-key-env OPENROUTER_API_KEY --model anthropic/claude-sonnet-4.5
```

| Field | Meaning |
|---|---|
| `headers_ms` | Request sent to response headers received |
| `ttft_ms` | Request sent to first token |
| `stream_span_ms` | First token to last token |
| `tok_per_s` | Generation speed |

- `--pad-tokens` adds filler to the prompt to imitate an agent-sized context.
- Each request includes a random nonce to defeat prompt caching. `--allow-cache`
  removes it.
- A long answer with `stream_span_ms` near 0 means something between you and the
  provider is buffering the stream.
- `--provider openai` works with any OpenAI-compatible endpoint: OpenAI,
  OpenRouter, Gemini's OpenAI-compatible endpoint, or local servers.

Cursor routes requests through its own backend, which can't be called directly.
For Cursor, use this as the provider-only reference, and compare Cursor's
`turn_native` across machines.

## Experiment plan

Change one variable at a time. For each setup, do **3 or more runs of 12 calls**,
and **alternate setups (A, B, A, B)** rather than doing all of A first, so changes
in provider load over time cancel out. Compare medians and p90.

| Question | Keep fixed | Vary | Look at |
|---|---|---|---|
| Is security software slowing process startup? Does its cache expire? | machine | Windows vs WSL vs Linux VM; idle period | `spawn_bench` first/repeat, idle table, new vs same script |
| How much do the environment and shell cost per tool call? | harness, model | corp-win PowerShell / Git Bash / WSL / VM, Mac | `shell_init`, `exec_launch`, `py_init`, `shell_overhead` |
| Which harness has the least overhead? | environment, model | harness | `turn_native`, `shell_overhead`, `reused` |
| How much does the model matter? | environment, harness | model | `turn_native`, `llm_probe` time to first token and tokens/s |
| Is Cursor's "taking longer than normal" local or backend? | harness = Cursor, model | Mac vs corp-win, in the same hour | `turn_native`, `net_probe` issuer/HTTP/2/api2.cursor.sh, `llm_probe` buffering |

For the last question: similar `turn_native` on both machines points to Cursor's
backend or the model. A much higher value on Windows points to the local network
path (proxy, TLS inspection, no HTTP/2) or to the machine itself.

## Collecting and comparing results

All results are written under `runs/`, which is git-ignored:

- `runs/<run-id>/`: one agent run (`meta.json`, `prompt.txt`, `probe.jsonl`,
  `markers/`, and optionally `spawn.jsonl` and `launch.json`)
- `runs/spawn/`, `runs/net/`, `runs/llm/`: baseline results, one JSONL file per
  invocation

Copy `runs/` from every machine into one checkout, then:

```bash
python analyze.py                     # all agent runs + spawn/net/LLM comparisons
python analyze.py --run <run-id>      # per-call breakdown for one run
python analyze.py --csv summary.csv   # agent-run summary for spreadsheets
```

Agent-run summaries exclude the first call, which includes cold-start effects.
The per-run detail view includes it.

## Caveats

- **Linux timing resolution:** process creation times on Linux (including WSL and
  VMs) are only accurate to about 10 ms. Windows and macOS are accurate to under
  1 ms.
- **Windows PowerShell 5.1 clock:** `[DateTime]::UtcNow` may be coarser there
  than in pwsh 7.
- **Windows virtual environment launcher:** `.venv\Scripts\python.exe` starts the
  base interpreter as a second process. That extra hop shows up in
  `exec_launch`. Use `--python` with the base interpreter to avoid it, or keep it
  to match normal use.
- **Marker timestamps:** they assume the harness writes files straight to disk.
  Check the marker count in `analyze.py --run`.
- **`turn_native` isn't pure model time:** it still includes harness overhead,
  such as noticing that the previous command finished. Use `llm_probe.py` as the
  provider-only reference.
- **Token parity is approximate:** the WRITE and RUN steps' output token counts
  are close but not identical, and harnesses wrap tools differently.
- **Older bash:** macOS's built-in bash 3.2 has no `$EPOCHREALTIME`. `shell_init`
  is then unavailable, and `turn_shell` falls back to Python's creation time.
  Git Bash and Linux bash 5 are fine.

## Project layout

```text
harnessbench/      shared library: process info, timing, run storage, stats, analysis
probe.py           agent-invoked tool-call probe
make_prompt.py     creates a run and its prompt
spawn_bench.py     process-spawn baseline
net_probe.py       network-path baseline
llm_probe.py       direct LLM API baseline
launch.py          optional CLI-harness launcher
analyze.py         reports
tests/             pytest tests
runs/              results (git-ignored)
```
