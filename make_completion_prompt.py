"""Create a completion-detection benchmark run and print the prompt to paste into the harness.

Each RUN step executes complete_probe.py with one behavior variant (lots of output,
no trailing newline, progress bar, slow drip, stderr, non-zero exit, sleeps, a
background child holding the output open, ...). Every RUN is followed by the same
native WRITE step, so the gap "command exited -> next marker written" is
harness completion detection + a constant model turn. Variants run in shuffled
rounds so drift over the run affects all of them equally.

    python make_completion_prompt.py --harness cline-gui --model haiku-5.5 --env mac
    python make_completion_prompt.py ... --variants line many-lines bg-holds-output --reps 5
"""

import argparse
import random
from pathlib import Path

import complete_probe
from harnessbench import prompts, runs

EXTRA_RULES = [
    "Some commands print a lot, write to stderr, exit with an error, take several seconds, "
    "or leave a background process running. That is expected: wait for the command result, "
    "then go straight to the next step. Never check on, wait for, or kill background processes.",
]


def plan_steps(variants: list[str], reps: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    plan = []
    for _ in range(reps):
        plan += rng.sample(variants, len(variants))
    return [
        {"seq": seq, "variant": variant} for seq, variant in enumerate(plan, start=1)
    ]


def complete_command(args: argparse.Namespace, run_id: str, step: dict) -> str:
    script_args = f"--run {run_id} --seq {step['seq']} --variant {step['variant']}"
    return prompts.invoke(args.shell, args.python, "complete_probe.py", script_args)


def build_steps(
    args: argparse.Namespace, run_id: str, markers: Path, plan: list[dict]
) -> list[str]:
    steps: list[str] = []
    for step in plan:
        command = complete_command(args, run_id, step)
        steps.append(prompts.marker_step(markers, step["seq"], len(command)))
        steps.append(f"RUN: `{command}`")
    steps.append(prompts.marker_step(markers, len(plan) + 1, len(command)))
    return steps


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    prompts.add_common_args(parser)
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=tuple(complete_probe.VARIANTS),
        default=complete_probe.DEFAULT_VARIANTS,
        help=f"default: all except sleep-30; '{complete_probe.BASELINE}' is always included",
    )
    parser.add_argument(
        "--reps", type=int, default=3, help="rounds through the variants"
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="shuffle seed (default: random)"
    )
    args = parser.parse_args()
    if complete_probe.BASELINE not in args.variants:
        args.variants = [complete_probe.BASELINE, *args.variants]
    if args.seed is None:
        args.seed = random.randrange(1_000_000)
    return args


def main() -> int:
    args = parse_args()
    run_id = runs.new_run_id(args.env, args.harness, args.model, "completion")
    markers = runs.run_dir(run_id) / "markers"
    markers.mkdir(exist_ok=True)
    plan = plan_steps(args.variants, args.reps, args.seed)
    prompt = prompts.build_prompt(build_steps(args, run_id, markers, plan), EXTRA_RULES)
    prompts.save_run(
        run_id,
        args,
        prompt,
        {
            "benchmark": "completion",
            "calls": len(plan),
            "mode": "alternate",
            "variants": args.variants,
            "reps": args.reps,
            "seed": args.seed,
            "steps": plan,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
