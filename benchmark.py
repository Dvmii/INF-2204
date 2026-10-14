import csv
import os
import shutil
import subprocess
import time
from pathlib import Path


# -----------------------------
# Settings
# -----------------------------

INPUT_BAM = "input.bam"
RESULTS_FILE = "results.csv"
REPEATS = 5

TMP_ROOT = Path("tmp")
OUTPUT_ROOT = Path("output")


# Experiment 1:
# Fixed thread count, change memory per thread
MEMORY_EXPERIMENT = [
    {"threads": 4, "memory": "128M"},
    {"threads": 4, "memory": "256M"},
    {"threads": 4, "memory": "512M"},
    {"threads": 4, "memory": "1G"},
]


# Experiment 2:
# Change threads while keeping approximately
# the same total memory budget
THREAD_EXPERIMENT = [
    {"threads": 1, "memory": "4G"},
    {"threads": 2, "memory": "2G"},
    {"threads": 4, "memory": "1G"},
    {"threads": 8, "memory": "512M"},
]


# Experiment 3:
# Add values around the area where you see
# a possible performance cliff.
CLIFF_EXPERIMENT = [
    {"threads": 4, "memory": "128M"},
    {"threads": 4, "memory": "160M"},
    {"threads": 4, "memory": "192M"},
    {"threads": 4, "memory": "224M"},
    {"threads": 4, "memory": "256M"},
]


# -----------------------------
# Run one benchmark
# -----------------------------

def run_benchmark(experiment, threads, memory, repeat):
    run_name = f"{experiment}_{threads}t_{memory}_run{repeat}"

    temp_dir = TMP_ROOT / run_name
    output_file = OUTPUT_ROOT / f"{run_name}.bam"

    # Start clean
    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True)

    if output_file.exists():
        output_file.unlink()

    temp_prefix = temp_dir / "tmp"

    command = [
        "/usr/bin/time",
        "-v",
        "samtools",
        "sort",
        "-@",
        str(threads),
        "-m",
        memory,
        "-T",
        str(temp_prefix),
        "-o",
        str(output_file),
        INPUT_BAM,
    ]

    print(f"\nRunning: {run_name}")

    start = time.perf_counter()

    process = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    runtime = time.perf_counter() - start

    # Get peak memory from /usr/bin/time -v
    peak_memory_kb = None

    for line in process.stderr.splitlines():
        if "Maximum resident set size" in line:
            peak_memory_kb = int(line.split(":")[-1].strip())

    # Check whether samtools succeeded
    success = process.returncode == 0

    if success:
        check = subprocess.run(
            ["samtools", "quickcheck", str(output_file)]
        )

        success = check.returncode == 0

    print(
        f"  time: {runtime:.2f}s | "
        f"peak RAM: {peak_memory_kb} KB | "
        f"success: {success}"
    )

    # We do not need the sorted BAM after measuring it
    if output_file.exists():
        output_file.unlink()

    shutil.rmtree(temp_dir, ignore_errors=True)

    return {
        "experiment": experiment,
        "threads": threads,
        "memory": memory,
        "repeat": repeat,
        "runtime_seconds": round(runtime, 3),
        "peak_memory_kb": peak_memory_kb,
        "success": success,
    }


# -----------------------------
# Run experiment
# -----------------------------

def run_experiment(name, configs, writer, file):
    for config in configs:
        for repeat in range(1, REPEATS + 1):

            result = run_benchmark(
                name,
                config["threads"],
                config["memory"],
                repeat,
            )

            writer.writerow(result)

            # Save results immediately
            file.flush()


# -----------------------------
# Main
# -----------------------------

def main():
    TMP_ROOT.mkdir(exist_ok=True)
    OUTPUT_ROOT.mkdir(exist_ok=True)

    fieldnames = [
        "experiment",
        "threads",
        "memory",
        "repeat",
        "runtime_seconds",
        "peak_memory_kb",
        "success",
    ]

    with open(RESULTS_FILE, "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()

        run_experiment(
            "memory",
            MEMORY_EXPERIMENT,
            writer,
            file,
        )

        run_experiment(
            "threads",
            THREAD_EXPERIMENT,
            writer,
            file,
        )

        run_experiment(
            "cliff",
            CLIFF_EXPERIMENT,
            writer,
            file,
        )

    print(f"\nDone! Results saved to {RESULTS_FILE}")


if __name__ == "__main__":
    main()