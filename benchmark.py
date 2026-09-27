from pathlib import Path
import csv
import os
import random
import re
import shutil
import subprocess
import time


# ============================================================
# CONFIGURATION
# ============================================================

INPUT_BAM = Path("data/patient1.bam")

RESULTS_DIR = Path("results")
RAW_DIR = RESULTS_DIR / "raw"
TEMP_ROOT = Path("benchmark_tmp")
OUTPUT_ROOT = Path("benchmark_outputs")

RESULTS_CSV = RESULTS_DIR / "results.csv"
SYSTEM_INFO = RESULTS_DIR / "system_info.txt"


# True = quick check that the benchmark works.
# False = run the full experiment campaign.
TEST_MODE = True

# Each final configuration is repeated 5 times.
FINAL_REPEATS = 5


# Experiment 1:
# Keep -@ fixed at 4 threads and vary -m.
# This gives the broad picture of how memory per thread affects
# runtime, peak memory use and temporary file activity.
MEMORY_THREADS = 4

MEMORY_VALUES_MB = [
    64,
    128,
    256,
    512,
    768,
    1024,
]


# Experiment 2:
# Vary -@ while keeping the nominal memory budget close to 3072 MiB.
# Each tuple is: (threads, memory per thread in MiB).
#
# Example:
# 8 threads * 384 MiB = 3072 MiB nominal memory.
THREAD_CONFIGS = [
    (1, 3072),
    (2, 1536),
    (4, 768),
    (8, 384),
    (16, 192),
]


# Experiment 3:
# Zooms in on the transition where temporary files disappear.
# Keep -@ fixed and vary -m in a smaller range.
#
# PS: insert found values before running experiment!   <---- !
CLIFF_THREADS = 4

CLIFF_MEMORY_VALUES_MB = [
    512,
    576,
    640,
    704,
    768,
]


# ============================================================
# SETUP
# ============================================================

RESULTS_DIR.mkdir(exist_ok=True)
RAW_DIR.mkdir(exist_ok=True)
TEMP_ROOT.mkdir(exist_ok=True)
OUTPUT_ROOT.mkdir(exist_ok=True)

if not INPUT_BAM.exists():
    raise FileNotFoundError(
        f"Input BAM not found: {INPUT_BAM}"
    )


# ============================================================
# SYSTEM INFORMATION
# ============================================================

def save_system_info():
    """Saves the machine and software information used for the run."""

    commands = [
        ["uname", "-a"],
        ["lscpu"],
        ["free", "-h"],
        ["df", "-hT", "."],
        ["samtools", "--version"],
    ]

    with open(SYSTEM_INFO, "w") as file:
        for command in commands:
            file.write("\n")
            file.write("$ " + " ".join(command) + "\n")
            file.write("=" * 60 + "\n")

            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            file.write(result.stdout)


# ============================================================
# READ GNU TIME RESULTS
# ============================================================

def parse_time_file(path):
    """Reads the measurements used from /usr/bin/time -v."""

    values = {}

    with open(path) as file:
        for line in file:
            line = line.strip()

            if ":" not in line:
                continue

            key, value = line.rsplit(":", 1)
            values[key.strip()] = value.strip()

    return {
        "max_rss_kb": int(
            values.get("Maximum resident set size (kbytes)", 0)
        ),
        "user_time_s": float(
            values.get("User time (seconds)", 0)
        ),
        "system_time_s": float(
            values.get("System time (seconds)", 0)
        ),
        "filesystem_inputs": int(
            values.get("File system inputs", 0)
        ),
        "filesystem_outputs": int(
            values.get("File system outputs", 0)
        ),
    }


# ============================================================
# READ SAMTOOLS OUTPUT
# ============================================================

def parse_samtools_stderr(path):
    """
    Reads the number of temporary files and in-memory blocks from SAMtools.
    """

    with open(path) as file:
        text = file.read()

    match = re.search(
        r"merging from (\d+) files and (\d+) in-memory blocks",
        text,
    )

    if match:
        return {
            "temp_files": int(match.group(1)),
            "in_memory_blocks": int(match.group(2)),
        }

    return {
        "temp_files": 0,
        "in_memory_blocks": 0,
    }


# ============================================================
# RUN ONE CONFIGURATION
# ============================================================

def run_one(
    experiment,
    repeat,
    threads,
    memory_mb,
    run_number,
):
    """Runs one samtools sort and returns its measurements."""

    run_id = (
        f"{run_number:03d}_"
        f"{experiment}_"
        f"rep{repeat}_"
        f"t{threads}_"
        f"m{memory_mb}"
    )

    print()
    print("=" * 65)
    print(f"RUN {run_number}: {run_id}")
    print(
        f"-@ {threads}, "
        f"-m {memory_mb}M, "
        f"nominal memory = {threads * memory_mb} MiB"
    )
    print("=" * 65)

    # Each run gets its own temp folder so files from different runs
    # cannot get mixed together.
    temp_dir = TEMP_ROOT / run_id
    temp_dir.mkdir(parents=True, exist_ok=True)

    output_bam = OUTPUT_ROOT / f"{run_id}.bam"
    time_file = RAW_DIR / f"{run_id}_time.txt"
    stderr_file = RAW_DIR / f"{run_id}_samtools.txt"

    # Finish pending writes before starting the next measurement.
    # This does not clear the Linux page cache.
    subprocess.run(["sync"])

    command = [
        "/usr/bin/time",
        "-v",
        "-o",
        str(time_file),
        "samtools",
        "sort",
        "-@",
        str(threads),
        "-m",
        f"{memory_mb}M",
        "-T",
        str(temp_dir / "temp"),
        "-o",
        str(output_bam),
        str(INPUT_BAM),
    ]

    # Python measures wall time directly. During the first WSL tests,
    # GNU time gave strange CPU/time values, so wall time is kept separate.
    start = time.perf_counter()

    with open(stderr_file, "w") as stderr:
        process = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
        )

    wall_time_s = time.perf_counter() - start

    timing = parse_time_file(time_file)
    sorting = parse_samtools_stderr(stderr_file)

    # Checks that SAMtools actually produced a valid BAM file.
    valid = False

    if process.returncode == 0 and output_bam.exists():
        check = subprocess.run(
            [
                "samtools",
                "quickcheck",
                "-v",
                str(output_bam),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        valid = check.returncode == 0

    input_bytes = INPUT_BAM.stat().st_size

    output_bytes = (
        output_bam.stat().st_size
        if output_bam.exists()
        else 0
    )

    throughput_mb_s = (
        input_bytes / (1024 ** 2) / wall_time_s
        if wall_time_s > 0
        else 0
    )

    result = {
        "run_number": run_number,
        "experiment": experiment,
        "repeat": repeat,
        "threads": threads,
        "memory_per_thread_mb": memory_mb,
        "nominal_memory_mb": threads * memory_mb,
        "wall_time_s": round(wall_time_s, 4),
        "throughput_mb_s": round(throughput_mb_s, 4),
        "max_rss_kb": timing["max_rss_kb"],
        "user_time_s": timing["user_time_s"],
        "system_time_s": timing["system_time_s"],
        "filesystem_inputs": timing["filesystem_inputs"],
        "filesystem_outputs": timing["filesystem_outputs"],
        "temp_files": sorting["temp_files"],
        "in_memory_blocks": sorting["in_memory_blocks"],
        "input_bytes": input_bytes,
        "output_bytes": output_bytes,
        "valid": valid,
        "exit_code": process.returncode,
    }

    print(f"wall time: {wall_time_s:.2f} s")
    print(f"peak RSS: {timing['max_rss_kb'] / 1024:.1f} MiB")
    print(f"temp files: {sorting['temp_files']}")
    print(f"in-memory blocks: {sorting['in_memory_blocks']}")
    print(f"output valid: {valid}")

    # We only need the measurements, not a sorted BAM from every run.
    if output_bam.exists():
        output_bam.unlink()

    shutil.rmtree(
        temp_dir,
        ignore_errors=True,
    )

    return result


# ============================================================
# CSV
# ============================================================

CSV_COLUMNS = [
    "run_number",
    "experiment",
    "repeat",
    "threads",
    "memory_per_thread_mb",
    "nominal_memory_mb",
    "wall_time_s",
    "throughput_mb_s",
    "max_rss_kb",
    "user_time_s",
    "system_time_s",
    "filesystem_inputs",
    "filesystem_outputs",
    "temp_files",
    "in_memory_blocks",
    "input_bytes",
    "output_bytes",
    "valid",
    "exit_code",
]


def save_result(writer, csv_file, result):
    """Save a run immediately so completed results are not lost."""

    writer.writerow(result)
    csv_file.flush()
    os.fsync(csv_file.fileno())


# ============================================================
# BUILD THE RUN LIST
# ============================================================

def build_runs():
    """Create the list of configurations the benchmark will run."""

    if TEST_MODE:
        # Two quick runs are enough to check that timing, RAM,
        # temp-file parsing, validation and CSV output all work.
        return [
            {
                "experiment": "test",
                "repeat": 1,
                "threads": 4,
                "memory_mb": 128,
            },
            {
                "experiment": "test",
                "repeat": 1,
                "threads": 4,
                "memory_mb": 768,
            },
        ]

    runs = []

    for repeat in range(1, FINAL_REPEATS + 1):
        current_repeat = []

        # Experiment 1: broad memory sweep.
        for memory_mb in MEMORY_VALUES_MB:
            current_repeat.append({
                "experiment": "memory_scaling",
                "repeat": repeat,
                "threads": MEMORY_THREADS,
                "memory_mb": memory_mb,
            })

        # Experiment 2: thread scaling with a fixed nominal memory budget.
        for threads, memory_mb in THREAD_CONFIGS:
            current_repeat.append({
                "experiment": "thread_scaling",
                "repeat": repeat,
                "threads": threads,
                "memory_mb": memory_mb,
            })

        # Experiment 3: smaller memory steps around the expected cliff.
        for memory_mb in CLIFF_MEMORY_VALUES_MB:
            current_repeat.append({
                "experiment": "performance_cliff",
                "repeat": repeat,
                "threads": CLIFF_THREADS,
                "memory_mb": memory_mb,
            })

        # The order is shuffled so the same configurations are not always
        # tested early or late in a repetition. The fixed seed makes the
        # order reproducible.
        rng = random.Random(2204 + repeat)
        rng.shuffle(current_repeat)

        runs.extend(current_repeat)

    return runs


# ============================================================
# MAIN
# ============================================================

def main():
    print()
    print("INF-2204 SAMtools benchmark")
    print("---------------------------")
    print(f"Input: {INPUT_BAM}")
    print(
        f"Input size: "
        f"{INPUT_BAM.stat().st_size / 1024**2:.1f} MiB"
    )

    if TEST_MODE:
        print("MODE: TEST")
    else:
        print("MODE: FINAL EXPERIMENT")

    save_system_info()
    runs = build_runs()

    print(f"Number of measured runs: {len(runs)}")

    # A new campaign starts with a new CSV file.
    with open(
        RESULTS_CSV,
        "w",
        newline="",
    ) as csv_file:

        writer = csv.DictWriter(
            csv_file,
            fieldnames=CSV_COLUMNS,
        )

        writer.writeheader()
        csv_file.flush()

        for run_number, config in enumerate(runs, start=1):
            result = run_one(
                experiment=config["experiment"],
                repeat=config["repeat"],
                threads=config["threads"],
                memory_mb=config["memory_mb"],
                run_number=run_number,
            )

            save_result(
                writer,
                csv_file,
                result,
            )

    print()
    print("=" * 65)
    print("BENCHMARK COMPLETE")
    print(f"Results: {RESULTS_CSV}")
    print("=" * 65)


if __name__ == "__main__":
    main()
