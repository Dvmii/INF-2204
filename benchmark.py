import argparse
import csv
import os
import random
import re
import shutil
import subprocess
import time
from pathlib import Path


# ============================================================
# SETTINGS
# ============================================================

INPUT_BAM = Path("data/patient1.bam")

RESULTS_ROOT = Path("results")
TMP_ROOT = Path("tmp")
OUTPUT_ROOT = Path("output")

REPEATS = 40
RANDOM_SEED = 2204
SETTLE_SECONDS = 2


# Experiment 1:
# Keep thread count fixed and change memory per thread.
MEMORY_EXPERIMENT = [
    {"threads": 4, "memory_mib": 64},
    {"threads": 4, "memory_mib": 128},
    {"threads": 4, "memory_mib": 256},
    {"threads": 4, "memory_mib": 512},
    {"threads": 4, "memory_mib": 768},
    {"threads": 4, "memory_mib": 1024},
]


# Experiment 2:
# Change thread count while keeping the planned sorting memory
# close to 3072 MiB.
THREAD_EXPERIMENT = [
    {"threads": 1, "memory_mib": 3072},
    {"threads": 2, "memory_mib": 1536},
    {"threads": 4, "memory_mib": 768},
    {"threads": 8, "memory_mib": 384},
    {"threads": 16, "memory_mib": 192},
]


# Experiment 3:
# Look more closely at the area where temporary files disappear.
# Change this range later if the broad memory experiment shows
# that the transition is somewhere else.
CLIFF_EXPERIMENT = [
    {"threads": 4, "memory_mib": memory}
    for memory in range(512, 769, 32)
]


RESULT_COLUMNS = [
    "run_id",
    "experiment",
    "repeat",
    "threads",
    "memory_per_thread_mib",
    "nominal_sort_memory_mib",
    "runtime_seconds",
    "throughput_mib_s",
    "peak_memory_mib",
    "user_time_seconds",
    "system_time_seconds",
    "filesystem_inputs",
    "filesystem_outputs",
    "temp_files",
    "in_memory_blocks",
    "disk_merge_input_fraction",
    "success",
]


# ============================================================
# BASIC SETUP
# ============================================================

def check_setup():
    """Check that the input BAM and required programs are available."""

    if not INPUT_BAM.exists():
        raise FileNotFoundError(f"Input BAM not found: {INPUT_BAM}")

    if shutil.which("samtools") is None:
        raise RuntimeError("samtools is not available on PATH")

    if not Path("/usr/bin/time").exists():
        raise RuntimeError("/usr/bin/time was not found")

    check = subprocess.run(
        ["samtools", "quickcheck", "-v", str(INPUT_BAM)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if check.returncode != 0:
        raise RuntimeError(
            "Input BAM failed samtools quickcheck:\n"
            + check.stdout
            + check.stderr
        )


def run_text(command):
    """Run a command and return its output as text."""

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    return result.stdout


def save_system_info(path):
    """Save the main hardware and software information."""

    commands = [
        ["uname", "-a"],
        ["lscpu"],
        ["free", "-h"],
        ["df", "-hT", "."],
        ["samtools", "--version"],
        ["python3", "--version"],
    ]

    with path.open("w") as file:
        for command in commands:
            file.write("$ " + " ".join(command) + "\n")
            file.write("=" * 60 + "\n")
            file.write(run_text(command))
            file.write("\n\n")


def save_dataset_info(path):
    """Save basic information about the BAM workload."""

    header = run_text(["samtools", "view", "-H", str(INPUT_BAM)])
    count = run_text(["samtools", "view", "-c", str(INPUT_BAM)]).strip()
    flagstat = run_text(["samtools", "flagstat", str(INPUT_BAM)])

    sort_order = "unknown"

    for line in header.splitlines():
        if line.startswith("@HD"):
            for field in line.split("\t"):
                if field.startswith("SO:"):
                    sort_order = field[3:]

    with path.open("w") as file:
        file.write(f"path: {INPUT_BAM.resolve()}\n")
        file.write(f"size_mib: {INPUT_BAM.stat().st_size / 1024**2:.2f}\n")
        file.write(f"alignment_records: {count}\n")
        file.write(f"header_sort_order: {sort_order}\n")
        file.write("\n$ samtools flagstat\n")
        file.write("=" * 60 + "\n")
        file.write(flagstat)


def warm_input_cache():
    """Read the input once so all measured runs use a warm-cache policy."""

    print("Warming input file cache...")

    with INPUT_BAM.open("rb") as file:
        while file.read(8 * 1024 * 1024):
            pass


# ============================================================
# PARSING MEASUREMENTS
# ============================================================

def parse_time_output(text):
    """Read the measurements we use from /usr/bin/time -v."""

    wanted = {
        "Maximum resident set size (kbytes)": ("peak_memory_kib", int),
        "User time (seconds)": ("user_time_seconds", float),
        "System time (seconds)": ("system_time_seconds", float),
        "File system inputs": ("filesystem_inputs", int),
        "File system outputs": ("filesystem_outputs", int),
    }

    values = {
        "peak_memory_kib": 0,
        "user_time_seconds": 0.0,
        "system_time_seconds": 0.0,
        "filesystem_inputs": 0,
        "filesystem_outputs": 0,
    }

    for line in text.splitlines():
        line = line.strip()

        for key, (name, value_type) in wanted.items():
            prefix = key + ":"

            if line.startswith(prefix):
                try:
                    values[name] = value_type(line[len(prefix):].strip())
                except ValueError:
                    pass

                break

    return values


def parse_samtools_merge(text):
    """
    Read SAMtools' final merge message.

    None means the message could not be parsed.
    Zero means SAMtools actually reported zero temporary files.
    """

    matches = re.findall(
        r"merging from\s+(\d+)\s+files?\s+and\s+(\d+)\s+in-memory blocks?",
        text,
        flags=re.IGNORECASE,
    )

    if not matches:
        return None, None

    temp_files, in_memory_blocks = map(int, matches[-1])

    return temp_files, in_memory_blocks


# ============================================================
# RUN PLAN
# ============================================================

def build_runs(mode, repeats):
    """Build and randomize the runs for the selected mode."""

    if mode == "test":
        return [
            {
                "experiment": "test",
                "repeat": 1,
                "threads": 4,
                "memory_mib": 128,
            },
            {
                "experiment": "test",
                "repeat": 1,
                "threads": 4,
                "memory_mib": 768,
            },
        ]

    runs = []

    experiments = [
        ("memory", MEMORY_EXPERIMENT),
        ("threads", THREAD_EXPERIMENT),
        ("cliff", CLIFF_EXPERIMENT),
    ]

    # Each repetition contains every configuration once.
    # The order is shuffled so the same setting is not always
    # measured early or late in the campaign.
    for repeat in range(1, repeats + 1):
        block = []

        for experiment, configs in experiments:
            for config in configs:
                block.append(
                    {
                        "experiment": experiment,
                        "repeat": repeat,
                        "threads": config["threads"],
                        "memory_mib": config["memory_mib"],
                    }
                )

        random.Random(RANDOM_SEED + repeat).shuffle(block)
        runs.extend(block)

    return runs


# ============================================================
# RUN ONE BENCHMARK
# ============================================================

def run_benchmark(config, raw_dir):
    """Run one SAMtools configuration and return its measurements."""

    experiment = config["experiment"]
    repeat = config["repeat"]
    threads = config["threads"]
    memory_mib = config["memory_mib"]

    run_id = (
        f"{experiment}_rep{repeat:02d}_"
        f"t{threads}_m{memory_mib}"
    )

    temp_dir = TMP_ROOT / run_id
    output_file = OUTPUT_ROOT / f"{run_id}.bam"

    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    output_file.unlink(missing_ok=True)

    print()
    print("=" * 65)
    print(f"Running: {run_id}")
    print(
        f"-@ {threads}, -m {memory_mib}M, "
        f"nominal memory = {threads * memory_mib} MiB"
    )

    # Finish pending writes, then give the system a short moment to settle.
    subprocess.run(["sync"], check=False)
    time.sleep(SETTLE_SECONDS)

    command = [
        "/usr/bin/time",
        "-v",
        "samtools",
        "sort",
        "-@",
        str(threads),
        "-m",
        f"{memory_mib}M",
        "-T",
        str(temp_dir / "tmp"),
        "-o",
        str(output_file),
        str(INPUT_BAM),
    ]

    start = time.perf_counter()

    process = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    runtime = time.perf_counter() - start

    # Keep the raw output from GNU time and SAMtools for later inspection.
    (raw_dir / f"{run_id}.txt").write_text(
        process.stderr,
        encoding="utf-8",
        errors="replace",
    )

    timing = parse_time_output(process.stderr)
    temp_files, in_memory_blocks = parse_samtools_merge(process.stderr)

    success = process.returncode == 0 and output_file.exists()

    if success:
        check = subprocess.run(
            ["samtools", "quickcheck", "-v", str(output_file)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        success = check.returncode == 0

    throughput = (
        INPUT_BAM.stat().st_size / 1024**2 / runtime
        if runtime > 0
        else 0.0
    )

    if temp_files is not None and in_memory_blocks is not None:
        total_merge_inputs = temp_files + in_memory_blocks
        disk_fraction = (
            temp_files / total_merge_inputs
            if total_merge_inputs > 0
            else 0.0
        )
    else:
        disk_fraction = None

    result = {
        "run_id": run_id,
        "experiment": experiment,
        "repeat": repeat,
        "threads": threads,
        "memory_per_thread_mib": memory_mib,
        "nominal_sort_memory_mib": threads * memory_mib,
        "runtime_seconds": round(runtime, 4),
        "throughput_mib_s": round(throughput, 4),
        "peak_memory_mib": round(timing["peak_memory_kib"] / 1024, 2),
        "user_time_seconds": timing["user_time_seconds"],
        "system_time_seconds": timing["system_time_seconds"],
        "filesystem_inputs": timing["filesystem_inputs"],
        "filesystem_outputs": timing["filesystem_outputs"],
        "temp_files": temp_files,
        "in_memory_blocks": in_memory_blocks,
        "disk_merge_input_fraction": (
            round(disk_fraction, 4)
            if disk_fraction is not None
            else None
        ),
        "success": success,
    }

    print(f"time:             {runtime:.2f} s")
    print(f"throughput:       {throughput:.2f} MiB/s")
    print(f"peak RAM:         {result['peak_memory_mib']:.1f} MiB")
    print(f"temp files:       {temp_files}")
    print(f"in-memory blocks: {in_memory_blocks}")
    print(f"success:          {success}")

    # The sorted BAM is only needed for validation.
    output_file.unlink(missing_ok=True)
    shutil.rmtree(temp_dir, ignore_errors=True)

    return result


# ============================================================
# SAVE / RESUME
# ============================================================

def read_completed_runs(results_file):
    """Find runs that already completed successfully."""

    if not results_file.exists():
        return set()

    with results_file.open(newline="") as file:
        return {
            row["run_id"]
            for row in csv.DictReader(file)
            if row.get("success") == "True"
        }


def append_result(results_file, result):
    """Save one result immediately."""

    new_file = (
        not results_file.exists()
        or results_file.stat().st_size == 0
    )

    with results_file.open("a", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=RESULT_COLUMNS)

        if new_file:
            writer.writeheader()

        writer.writerow(result)
        file.flush()
        os.fsync(file.fileno())


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "mode",
        choices=["test", "final"],
        nargs="?",
        default="test",
    )

    parser.add_argument(
        "--repeats",
        type=int,
        default=REPEATS,
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--resume", action="store_true")
    group.add_argument("--overwrite", action="store_true")

    args = parser.parse_args()

    if args.repeats < 1:
        parser.error("--repeats must be at least 1")

    campaign_dir = RESULTS_ROOT / args.mode
    results_file = campaign_dir / "results.csv"

    if args.overwrite and campaign_dir.exists():
        shutil.rmtree(campaign_dir)

    campaign_dir.mkdir(parents=True, exist_ok=True)

    raw_dir = campaign_dir / "raw"
    raw_dir.mkdir(exist_ok=True)

    TMP_ROOT.mkdir(exist_ok=True)
    OUTPUT_ROOT.mkdir(exist_ok=True)

    if results_file.exists() and not args.resume and not args.overwrite:
        raise FileExistsError(
            f"{results_file} already exists. "
            "Use --resume to continue or --overwrite to start again."
        )

    check_setup()

    save_system_info(campaign_dir / "system_info.txt")
    save_dataset_info(campaign_dir / "dataset_info.txt")

    runs = build_runs(args.mode, args.repeats)

    completed = (
        read_completed_runs(results_file)
        if args.resume
        else set()
    )

    print()
    print("INF-2204 SAMtools benchmark")
    print("=" * 65)
    print(f"Mode: {args.mode}")
    print(f"Input: {INPUT_BAM}")
    print(f"Planned runs: {len(runs)}")
    print(f"Already completed: {len(completed)}")
    print("=" * 65)

    warm_input_cache()

    for config in runs:
        run_id = (
            f"{config['experiment']}_rep{config['repeat']:02d}_"
            f"t{config['threads']}_m{config['memory_mib']}"
        )

        if run_id in completed:
            print(f"SKIP completed: {run_id}")
            continue

        result = run_benchmark(config, raw_dir)
        append_result(results_file, result)

        if not result["success"]:
            raise RuntimeError(
                f"Run {result['run_id']} failed. "
                "Fix the problem before continuing."
            )

    print()
    print("=" * 65)
    print("BENCHMARK COMPLETE")
    print(f"Results:      {results_file}")
    print(f"Dataset info: {campaign_dir / 'dataset_info.txt'}")
    print(f"System info:  {campaign_dir / 'system_info.txt'}")
    print("=" * 65)


if __name__ == "__main__":
    main()
