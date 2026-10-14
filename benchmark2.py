#!/usr/bin/env python3
"""Reproducible SAMtools sort benchmark for INF-2204.

The script automates the full experimental campaign:
- records software/hardware and dataset characteristics,
- creates a deterministic randomized run plan,
- repeats every configuration many times,
- measures wall time, RSS, filesystem I/O and SAMtools spill/merge activity,
- validates every produced BAM,
- saves every run immediately so a long campaign can be resumed,
- writes a per-configuration statistical summary and baseline comparisons.

Typical use:
    python3 benchmark_improved.py test --overwrite
    python3 benchmark_improved.py final --repeats 30 --overwrite
    python3 benchmark_improved.py final --repeats 30 --resume

The benchmark uses only the Python standard library plus external commands
already required by the project (samtools and /usr/bin/time).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Iterable


# ============================================================
# EXPERIMENT CONFIGURATION
# ============================================================

DEFAULT_INPUT_BAM = Path("data/patient1.bam")
DEFAULT_RESULTS_ROOT = Path("results")
TEMP_ROOT = Path("benchmark_tmp")
OUTPUT_ROOT = Path("benchmark_outputs")

# Teacher feedback asked for substantially more repetition than five runs.
# 30 is the default; use --repeats 40 if time permits.
DEFAULT_FINAL_REPEATS = 30
DEFAULT_TEST_REPEATS = 1
RANDOM_SEED = 2204

# Experiment 1: fixed thread count, broad memory sweep.
MEMORY_THREADS = 4
MEMORY_VALUES_MIB = [64, 128, 256, 512, 768, 1024]

# Experiment 2: vary thread count while holding the nominal sorting-memory
# budget at approximately 3072 MiB.  (-m is documented per thread.)
THREAD_CONFIGS = [
    (1, 3072),
    (2, 1536),
    (4, 768),
    (8, 384),
    (16, 192),
]

# Experiment 3: fine-grained sweep around the expected spill/no-spill region.
# The 32 MiB step is deliberately finer than experiment 1.  It yields nine
# levels across the team's current 512-768 MiB bracket, enough resolution to localise
# a transition without making the campaign unmanageably large.  If a quick
# test shows the transition lies outside this bracket, change LOW/HIGH before
# the final campaign.
CLIFF_THREADS = 4
CLIFF_LOW_MIB = 512
CLIFF_HIGH_MIB = 768
CLIFF_STEP_MIB = 32
CLIFF_MEMORY_VALUES_MIB = list(
    range(CLIFF_LOW_MIB, CLIFF_HIGH_MIB + CLIFF_STEP_MIB, CLIFF_STEP_MIB)
)

# Baselines used only for summary/comparison metrics.
# - memory scaling: SAMtools' default -m value at the same thread count
# - thread scaling: the one-thread point under the fixed nominal budget
# - performance cliff: highest-memory point in the local sweep
BASELINE_BY_EXPERIMENT = {
    "memory_scaling": (MEMORY_THREADS, 768),
    "thread_scaling": (1, 3072),
    "performance_cliff": (CLIFF_THREADS, max(CLIFF_MEMORY_VALUES_MIB)),
}

# Environmental control.  These checks only cover the Linux/WSL guest; Windows
# host activity can still be a confounder and should be controlled manually.
SETTLE_SECONDS = 2.0
IDLE_CPU_THRESHOLD_PERCENT = 20.0
IDLE_CHECK_INTERVAL_SECONDS = 2.0
MAX_IDLE_WAIT_SECONDS = 120.0
MIN_AVAILABLE_MEMORY_MIB = 2048.0
MIN_FREE_DISK_FACTOR = 3.0  # free space relative to compressed input size

# "warm" gives every measured run the same intended page-cache policy: the
# input is read once before the campaign and caches are NOT dropped between
# runs.  "cold" is available with --cache-policy cold and requires passwordless
# sudo permission to drop Linux caches.
DEFAULT_CACHE_POLICY = "warm"

TIME_BIN = Path("/usr/bin/time")


# ============================================================
# HELPERS
# ============================================================


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_capture(command: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            chunk = file.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def read_mem_available_mib() -> float:
    try:
        with open("/proc/meminfo") as file:
            for line in file:
                if line.startswith("MemAvailable:"):
                    kib = float(line.split()[1])
                    return kib / 1024.0
    except OSError:
        pass
    return math.nan


def read_cpu_times() -> tuple[int, int]:
    """Return (idle_jiffies, total_jiffies) from /proc/stat."""
    with open("/proc/stat") as file:
        fields = file.readline().split()[1:]
    values = [int(value) for value in fields]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return idle, sum(values)


def cpu_busy_percent(sample_seconds: float = 0.5) -> float:
    try:
        idle1, total1 = read_cpu_times()
        time.sleep(sample_seconds)
        idle2, total2 = read_cpu_times()
        total_delta = total2 - total1
        if total_delta <= 0:
            return math.nan
        idle_delta = idle2 - idle1
        return 100.0 * (1.0 - idle_delta / total_delta)
    except (OSError, ValueError):
        return math.nan


def mean_cpu_mhz() -> float:
    values: list[float] = []
    try:
        with open("/proc/cpuinfo") as file:
            for line in file:
                if line.lower().startswith("cpu mhz"):
                    values.append(float(line.split(":", 1)[1].strip()))
    except (OSError, ValueError):
        return math.nan
    return statistics.fmean(values) if values else math.nan


def wait_for_quiet_system() -> dict[str, float]:
    """Best-effort guard against obvious guest-side background activity."""
    deadline = time.monotonic() + MAX_IDLE_WAIT_SECONDS
    last_busy = math.nan
    last_mem = math.nan

    while True:
        last_busy = cpu_busy_percent()
        last_mem = read_mem_available_mib()

        cpu_ok = math.isnan(last_busy) or last_busy <= IDLE_CPU_THRESHOLD_PERCENT
        mem_ok = math.isnan(last_mem) or last_mem >= MIN_AVAILABLE_MEMORY_MIB

        if cpu_ok and mem_ok:
            break

        if time.monotonic() >= deadline:
            raise RuntimeError(
                "System did not become quiet enough before timeout: "
                f"CPU busy={last_busy:.1f}%, MemAvailable={last_mem:.0f} MiB. "
                "Close background workloads or relax the thresholds explicitly."
            )

        print(
            f"Waiting for quieter system: CPU={last_busy:.1f}% "
            f"MemAvailable={last_mem:.0f} MiB"
        )
        time.sleep(IDLE_CHECK_INTERVAL_SECONDS)

    if SETTLE_SECONDS > 0:
        time.sleep(SETTLE_SECONDS)

    return {
        "cpu_busy_before_percent": last_busy,
        "mem_available_before_mib": last_mem,
        "cpu_mhz_before": mean_cpu_mhz(),
    }


def check_disk_space(input_bam: Path, path: Path) -> None:
    free_bytes = shutil.disk_usage(path).free
    required = input_bam.stat().st_size * MIN_FREE_DISK_FACTOR
    if free_bytes < required:
        raise RuntimeError(
            f"Not enough free disk space under {path}: "
            f"{free_bytes / 1024**3:.1f} GiB free, at least "
            f"{required / 1024**3:.1f} GiB requested by safety factor."
        )


def configure_process_priority(target_nice: int | None) -> int | None:
    """Try to set Linux process niceness; children inherit it.

    Negative niceness (higher priority) normally requires privilege.  We do not
    silently escalate privileges.  If it cannot be set, the campaign aborts so
    the environment is not different from what the user requested.
    """
    if target_nice is None or not hasattr(os, "getpriority"):
        return None

    current = os.getpriority(os.PRIO_PROCESS, 0)
    if current == target_nice:
        return current

    try:
        os.setpriority(os.PRIO_PROCESS, 0, target_nice)
    except PermissionError as exc:
        raise PermissionError(
            f"Could not set niceness from {current} to {target_nice}. "
            "Negative niceness usually needs additional Linux privileges. "
            "Run without --nice or configure the privilege deliberately."
        ) from exc

    return os.getpriority(os.PRIO_PROCESS, 0)


def prepare_cache(input_bam: Path, policy: str) -> None:
    if policy == "warm":
        print("Cache policy: warm (reading input once before measured runs)")
        with input_bam.open("rb") as file:
            while file.read(8 * 1024 * 1024):
                pass
        return

    if policy == "cold":
        print("Cache policy: cold (dropping Linux page cache before each run)")
        result = subprocess.run(
            ["sudo", "-n", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Cold-cache policy requires passwordless sudo permission for "
                "dropping Linux page caches. stderr: " + result.stderr.strip()
            )
        return

    raise ValueError(f"Unknown cache policy: {policy}")


def drop_caches_if_requested(policy: str) -> None:
    if policy != "cold":
        return
    result = subprocess.run(
        ["sudo", "-n", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("Failed to drop Linux page caches before a run.")


# ============================================================
# REPRODUCIBILITY / DATASET DESCRIPTION
# ============================================================


def preflight(input_bam: Path) -> None:
    if not input_bam.exists():
        raise FileNotFoundError(f"Input BAM not found: {input_bam}")
    if not command_exists("samtools"):
        raise RuntimeError("samtools is not available on PATH")
    if not TIME_BIN.exists():
        raise RuntimeError(f"GNU time not found at {TIME_BIN}")

    quickcheck = subprocess.run(
        ["samtools", "quickcheck", "-v", str(input_bam)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if quickcheck.returncode != 0:
        raise RuntimeError(
            "Input BAM failed samtools quickcheck:\n"
            + quickcheck.stdout
            + quickcheck.stderr
        )


def save_system_info(path: Path, *, cache_policy: str, requested_nice: int | None) -> None:
    commands = [
        ["uname", "-a"],
        ["lscpu"],
        ["free", "-h"],
        ["df", "-hT", "."],
        ["lsblk", "-o", "NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,MODEL"],
        ["samtools", "--version"],
        [str(TIME_BIN), "--version"],
        [sys.executable, "--version"],
        ["git", "rev-parse", "HEAD"],
        ["git", "status", "--short"],
        ["ps", "-eo", "pid,ni,pcpu,pmem,comm", "--sort=-pcpu"],
    ]

    with path.open("w") as file:
        file.write(f"Recorded UTC: {utc_now()}\n")
        file.write(f"Cache policy: {cache_policy}\n")
        file.write(f"Requested niceness: {requested_nice}\n")
        if hasattr(os, "getpriority"):
            file.write(f"Actual niceness: {os.getpriority(os.PRIO_PROCESS, 0)}\n")
        file.write("\n")

        for command in commands:
            file.write("$ " + " ".join(command) + "\n")
            file.write("=" * 72 + "\n")
            try:
                result = run_capture(command)
                file.write(result.stdout)
                file.write(f"[exit code {result.returncode}]\n\n")
            except FileNotFoundError:
                file.write("[command unavailable]\n\n")


def parse_header_characteristics(header: str) -> dict[str, Any]:
    sort_order = "unknown"
    references = 0
    reference_bases = 0
    read_groups = 0

    for line in header.splitlines():
        fields = line.split("\t")
        if not fields:
            continue
        if fields[0] == "@HD":
            for field in fields[1:]:
                if field.startswith("SO:"):
                    sort_order = field[3:]
        elif fields[0] == "@SQ":
            references += 1
            for field in fields[1:]:
                if field.startswith("LN:"):
                    try:
                        reference_bases += int(field[3:])
                    except ValueError:
                        pass
        elif fields[0] == "@RG":
            read_groups += 1

    return {
        "header_sort_order": sort_order,
        "reference_sequences": references,
        "reference_bases": reference_bases,
        "read_groups": read_groups,
    }


def save_dataset_info(input_bam: Path, txt_path: Path, json_path: Path) -> dict[str, Any]:
    print("Profiling dataset (size, records, header, flagstat, SHA-256)...")

    header_result = run_capture(["samtools", "view", "-H", str(input_bam)], check=True)
    count_result = run_capture(["samtools", "view", "-c", str(input_bam)], check=True)
    flagstat_result = run_capture(["samtools", "flagstat", str(input_bam)], check=True)

    info: dict[str, Any] = {
        "path": str(input_bam),
        "size_bytes": input_bam.stat().st_size,
        "size_mib": input_bam.stat().st_size / 1024**2,
        "alignment_records": int(count_result.stdout.strip()),
        "sha256": sha256_file(input_bam),
        **parse_header_characteristics(header_result.stdout),
        "flagstat": flagstat_result.stdout,
    }

    json_path.write_text(json.dumps(info, indent=2) + "\n")

    with txt_path.open("w") as file:
        file.write("Dataset characteristics\n")
        file.write("=" * 72 + "\n")
        for key in [
            "path",
            "size_bytes",
            "size_mib",
            "alignment_records",
            "header_sort_order",
            "reference_sequences",
            "reference_bases",
            "read_groups",
            "sha256",
        ]:
            file.write(f"{key}: {info[key]}\n")
        file.write("\n$ samtools flagstat\n")
        file.write("=" * 72 + "\n")
        file.write(info["flagstat"])

    return info


def measure_instrumentation_overhead(path: Path, iterations: int = 30) -> None:
    """Estimate the overhead added by wrapping a tiny process in GNU time.

    The value is diagnostic only; it is not subtracted from measured sort time.
    For a long-running sort it should be tiny relative to the workload.
    """
    true_bin = shutil.which("true")
    if true_bin is None:
        path.write_text("Instrumentation overhead not measured: true command missing\n")
        return

    direct: list[float] = []
    instrumented: list[float] = []
    scratch = path.with_suffix(".time.tmp")

    for _ in range(iterations):
        start = time.perf_counter()
        subprocess.run([true_bin], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        direct.append(time.perf_counter() - start)

        start = time.perf_counter()
        subprocess.run(
            [str(TIME_BIN), "-v", "-o", str(scratch), true_bin],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        instrumented.append(time.perf_counter() - start)

    scratch.unlink(missing_ok=True)
    direct_median = statistics.median(direct)
    instrumented_median = statistics.median(instrumented)
    overhead = instrumented_median - direct_median

    path.write_text(
        "GNU time wrapper overhead estimate\n"
        + "=" * 72
        + "\n"
        + f"iterations: {iterations}\n"
        + f"median direct process time_s: {direct_median:.9f}\n"
        + f"median instrumented process time_s: {instrumented_median:.9f}\n"
        + f"estimated wrapper overhead_s: {overhead:.9f}\n"
        + "This diagnostic is not subtracted from benchmark measurements.\n"
    )


# ============================================================
# MEASUREMENT PARSERS
# ============================================================


def parse_gnu_time(path: Path) -> dict[str, float | int]:
    """Read the fields used from /usr/bin/time -v output."""
    values: dict[str, str] = {}

    with path.open() as file:
        wanted_keys = [
            "Maximum resident set size (kbytes)",
            "User time (seconds)",
            "System time (seconds)",
            "File system inputs",
            "File system outputs",
            "Voluntary context switches",
            "Involuntary context switches",
            "Major (requiring I/O) page faults",
            "Minor (reclaiming a frame) page faults",
        ]
        for raw_line in file:
            line = raw_line.strip()
            for key in wanted_keys:
                prefix = key + ":"
                if line.startswith(prefix):
                    values[key] = line[len(prefix):].strip()
                    break

    def as_int(key: str) -> int:
        try:
            return int(values.get(key, "0"))
        except ValueError:
            return 0

    def as_float(key: str) -> float:
        try:
            return float(values.get(key, "0"))
        except ValueError:
            return 0.0

    return {
        "max_rss_kib": as_int("Maximum resident set size (kbytes)"),
        "user_time_s": as_float("User time (seconds)"),
        "system_time_s": as_float("System time (seconds)"),
        "filesystem_inputs": as_int("File system inputs"),
        "filesystem_outputs": as_int("File system outputs"),
        "voluntary_context_switches": as_int("Voluntary context switches"),
        "involuntary_context_switches": as_int("Involuntary context switches"),
        "major_page_faults": as_int("Major (requiring I/O) page faults"),
        "minor_page_faults": as_int("Minor (reclaiming a frame) page faults"),
    }


def parse_samtools_stderr(path: Path) -> dict[str, int | bool | None]:
    """Parse SAMtools' final merge message.

    Operational definition used in this project:
    - temp_files_for_final_merge: on-disk temporary runs reported by SAMtools
      as inputs to the final merge.
    - in_memory_blocks_for_final_merge: in-memory runs reported in the same
      message.
    - disk_merge_input_fraction: temp_files / (temp_files + in_memory_blocks),
      a count-based comparison metric (not a byte fraction).

    Missing parser output is represented as blank/None, never as a false zero.
    """
    text = path.read_text(errors="replace")
    matches = re.findall(
        r"merging from\s+(\d+)\s+files?\s+and\s+(\d+)\s+in-memory blocks?",
        text,
        flags=re.IGNORECASE,
    )

    if not matches:
        return {
            "temp_files_for_final_merge": None,
            "in_memory_blocks_for_final_merge": None,
            "temp_parse_ok": False,
            "merge_messages": 0,
        }

    temp_files, in_memory_blocks = map(int, matches[-1])
    return {
        "temp_files_for_final_merge": temp_files,
        "in_memory_blocks_for_final_merge": in_memory_blocks,
        "temp_parse_ok": True,
        "merge_messages": len(matches),
    }


# ============================================================
# RUN PLAN
# ============================================================


def build_runs(mode: str, repeats: int, seed: int) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []

    if mode == "test":
        runs = [
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

    elif mode == "final":
        # Randomized complete blocks: every repetition contains every final
        # configuration exactly once, then the order within that repetition is
        # shuffled deterministically.  This spreads time/temperature drift across
        # configurations rather than always favouring an early or late setting.
        for repeat in range(1, repeats + 1):
            block: list[dict[str, Any]] = []

            for memory_mib in MEMORY_VALUES_MIB:
                block.append(
                    {
                        "experiment": "memory_scaling",
                        "repeat": repeat,
                        "threads": MEMORY_THREADS,
                        "memory_mib": memory_mib,
                    }
                )

            for threads, memory_mib in THREAD_CONFIGS:
                block.append(
                    {
                        "experiment": "thread_scaling",
                        "repeat": repeat,
                        "threads": threads,
                        "memory_mib": memory_mib,
                    }
                )

            for memory_mib in CLIFF_MEMORY_VALUES_MIB:
                block.append(
                    {
                        "experiment": "performance_cliff",
                        "repeat": repeat,
                        "threads": CLIFF_THREADS,
                        "memory_mib": memory_mib,
                    }
                )

            rng = random.Random(seed + repeat)
            rng.shuffle(block)
            runs.extend(block)

    else:
        raise ValueError(f"Unsupported mode: {mode}")

    for run_number, run in enumerate(runs, start=1):
        run["run_number"] = run_number
        run["run_id"] = (
            f"{run_number:04d}_{run['experiment']}_rep{run['repeat']:02d}_"
            f"t{run['threads']}_m{run['memory_mib']}"
        )

    return runs


def save_run_plan(path: Path, runs: Iterable[dict[str, Any]]) -> None:
    fields = ["run_number", "run_id", "experiment", "repeat", "threads", "memory_mib"]
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for run in runs:
            writer.writerow({field: run[field] for field in fields})


# ============================================================
# ONE MEASURED RUN
# ============================================================


RESULT_COLUMNS = [
    "run_number",
    "run_id",
    "experiment",
    "repeat",
    "threads",
    "memory_per_thread_mib",
    "nominal_sort_memory_mib",
    "start_utc",
    "wall_time_s",
    "throughput_mib_s",
    "max_rss_kib",
    "max_rss_mib",
    "user_time_s",
    "system_time_s",
    "filesystem_inputs",
    "filesystem_outputs",
    "major_page_faults",
    "minor_page_faults",
    "voluntary_context_switches",
    "involuntary_context_switches",
    "temp_files_for_final_merge",
    "in_memory_blocks_for_final_merge",
    "disk_merge_input_fraction",
    "temp_parse_ok",
    "merge_messages",
    "cpu_busy_before_percent",
    "mem_available_before_mib",
    "cpu_mhz_before",
    "disk_free_before_gib",
    "input_bytes",
    "output_bytes",
    "output_valid",
    "exit_code",
    "status",
]


def run_one(
    config: dict[str, Any],
    *,
    input_bam: Path,
    raw_dir: Path,
    temp_root: Path,
    output_root: Path,
    cache_policy: str,
) -> dict[str, Any]:
    run_number = int(config["run_number"])
    run_id = str(config["run_id"])
    experiment = str(config["experiment"])
    repeat = int(config["repeat"])
    threads = int(config["threads"])
    memory_mib = int(config["memory_mib"])

    print()
    print("=" * 78)
    print(f"RUN {run_number}: {run_id}")
    print(
        f"-@ {threads}, -m {memory_mib}M, "
        f"nominal sorting memory = {threads * memory_mib} MiB"
    )
    print("=" * 78)

    temp_dir = temp_root / run_id
    output_bam = output_root / f"{run_id}.bam"
    time_file = raw_dir / f"{run_id}_time.txt"
    stderr_file = raw_dir / f"{run_id}_samtools.stderr.txt"

    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    output_bam.unlink(missing_ok=True)

    drop_caches_if_requested(cache_policy)
    # Flush writes from the previous run first, then wait until the guest is
    # quiet.  Doing this in the opposite order would let sync itself disturb
    # the start of the measured run.
    subprocess.run(["sync"], check=False)
    env_before = wait_for_quiet_system()

    disk_free_before_gib = shutil.disk_usage(output_root).free / 1024**3
    command = [
        str(TIME_BIN),
        "-v",
        "-o",
        str(time_file),
        "samtools",
        "sort",
        "-@",
        str(threads),
        "-m",
        f"{memory_mib}M",
        "-T",
        str(temp_dir / "temp"),
        "-o",
        str(output_bam),
        str(input_bam),
    ]

    start_utc = utc_now()
    start = time.perf_counter()

    with stderr_file.open("w") as stderr:
        process = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
        )

    wall_time_s = time.perf_counter() - start

    timing = parse_gnu_time(time_file) if time_file.exists() else {
        "max_rss_kib": 0,
        "user_time_s": 0.0,
        "system_time_s": 0.0,
        "filesystem_inputs": 0,
        "filesystem_outputs": 0,
        "voluntary_context_switches": 0,
        "involuntary_context_switches": 0,
        "major_page_faults": 0,
        "minor_page_faults": 0,
    }
    sorting = parse_samtools_stderr(stderr_file)

    output_valid = False
    if process.returncode == 0 and output_bam.exists():
        check = subprocess.run(
            ["samtools", "quickcheck", "-v", str(output_bam)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        output_valid = check.returncode == 0

    input_bytes = input_bam.stat().st_size
    output_bytes = output_bam.stat().st_size if output_bam.exists() else 0
    throughput_mib_s = (
        input_bytes / 1024**2 / wall_time_s if wall_time_s > 0 else math.nan
    )

    temp_files = sorting["temp_files_for_final_merge"]
    memory_blocks = sorting["in_memory_blocks_for_final_merge"]
    if isinstance(temp_files, int) and isinstance(memory_blocks, int):
        denominator = temp_files + memory_blocks
        disk_fraction: float | None = temp_files / denominator if denominator else 0.0
    else:
        disk_fraction = None

    status = "ok" if process.returncode == 0 and output_valid else "failed"

    result: dict[str, Any] = {
        "run_number": run_number,
        "run_id": run_id,
        "experiment": experiment,
        "repeat": repeat,
        "threads": threads,
        "memory_per_thread_mib": memory_mib,
        "nominal_sort_memory_mib": threads * memory_mib,
        "start_utc": start_utc,
        "wall_time_s": round(wall_time_s, 6),
        "throughput_mib_s": round(throughput_mib_s, 6),
        "max_rss_kib": timing["max_rss_kib"],
        "max_rss_mib": round(float(timing["max_rss_kib"]) / 1024.0, 3),
        "user_time_s": timing["user_time_s"],
        "system_time_s": timing["system_time_s"],
        "filesystem_inputs": timing["filesystem_inputs"],
        "filesystem_outputs": timing["filesystem_outputs"],
        "major_page_faults": timing["major_page_faults"],
        "minor_page_faults": timing["minor_page_faults"],
        "voluntary_context_switches": timing["voluntary_context_switches"],
        "involuntary_context_switches": timing["involuntary_context_switches"],
        "temp_files_for_final_merge": temp_files,
        "in_memory_blocks_for_final_merge": memory_blocks,
        "disk_merge_input_fraction": (
            round(disk_fraction, 6) if disk_fraction is not None else None
        ),
        "temp_parse_ok": sorting["temp_parse_ok"],
        "merge_messages": sorting["merge_messages"],
        "cpu_busy_before_percent": round(env_before["cpu_busy_before_percent"], 3),
        "mem_available_before_mib": round(env_before["mem_available_before_mib"], 3),
        "cpu_mhz_before": round(env_before["cpu_mhz_before"], 3),
        "disk_free_before_gib": round(disk_free_before_gib, 3),
        "input_bytes": input_bytes,
        "output_bytes": output_bytes,
        "output_valid": output_valid,
        "exit_code": process.returncode,
        "status": status,
    }

    print(f"wall time:       {wall_time_s:.2f} s")
    print(f"throughput:      {throughput_mib_s:.2f} MiB/s")
    print(f"peak RSS:        {float(timing['max_rss_kib']) / 1024:.1f} MiB")
    print(f"temp files:      {temp_files if temp_files is not None else 'PARSE FAILED'}")
    print(f"in-memory blocks:{memory_blocks if memory_blocks is not None else ' PARSE FAILED'}")
    print(f"output valid:    {output_valid}")

    output_bam.unlink(missing_ok=True)
    shutil.rmtree(temp_dir, ignore_errors=True)

    return result


# ============================================================
# PERSISTENCE / RESUME
# ============================================================


def read_completed_run_ids(results_csv: Path) -> set[str]:
    if not results_csv.exists():
        return set()
    with results_csv.open(newline="") as file:
        return {
            row["run_id"]
            for row in csv.DictReader(file)
            if row.get("status") == "ok" and row.get("run_id")
        }


def append_result(results_csv: Path, result: dict[str, Any]) -> None:
    is_new = not results_csv.exists() or results_csv.stat().st_size == 0
    with results_csv.open("a", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=RESULT_COLUMNS)
        if is_new:
            writer.writeheader()
        writer.writerow(result)
        file.flush()
        os.fsync(file.fileno())


# ============================================================
# SUMMARY / COMPARISON METRICS
# ============================================================


def parse_optional_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def median_or_none(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def stdev_or_none(values: list[float]) -> float | None:
    return statistics.stdev(values) if len(values) >= 2 else None


def create_summary(results_csv: Path, summary_csv: Path) -> None:
    with results_csv.open(newline="") as file:
        rows = [row for row in csv.DictReader(file) if row.get("status") == "ok"]

    grouped: dict[tuple[str, int, int], list[dict[str, str]]] = {}
    for row in rows:
        key = (
            row["experiment"],
            int(row["threads"]),
            int(row["memory_per_thread_mib"]),
        )
        grouped.setdefault(key, []).append(row)

    stats_by_key: dict[tuple[str, int, int], dict[str, Any]] = {}
    for key, group in grouped.items():
        wall = [float(row["wall_time_s"]) for row in group]
        throughput = [float(row["throughput_mib_s"]) for row in group]
        rss = [float(row["max_rss_mib"]) for row in group]
        temp = [
            value
            for row in group
            if (value := parse_optional_float(row.get("temp_files_for_final_merge")))
            is not None
        ]
        disk_fraction = [
            value
            for row in group
            if (value := parse_optional_float(row.get("disk_merge_input_fraction")))
            is not None
        ]

        wall_mean = statistics.fmean(wall)
        wall_sd = stdev_or_none(wall)
        stats_by_key[key] = {
            "n": len(group),
            "wall_time_mean_s": wall_mean,
            "wall_time_median_s": statistics.median(wall),
            "wall_time_sd_s": wall_sd,
            "wall_time_cv_percent": (
                100.0 * wall_sd / wall_mean if wall_sd is not None and wall_mean else None
            ),
            "wall_time_min_s": min(wall),
            "wall_time_max_s": max(wall),
            "throughput_mean_mib_s": statistics.fmean(throughput),
            "max_rss_mean_mib": statistics.fmean(rss),
            "temp_files_median": median_or_none(temp),
            "temp_files_min": min(temp) if temp else None,
            "temp_files_max": max(temp) if temp else None,
            "disk_merge_input_fraction_median": median_or_none(disk_fraction),
        }

    fields = [
        "experiment",
        "threads",
        "memory_per_thread_mib",
        "nominal_sort_memory_mib",
        "n",
        "wall_time_mean_s",
        "wall_time_median_s",
        "wall_time_sd_s",
        "wall_time_cv_percent",
        "wall_time_min_s",
        "wall_time_max_s",
        "throughput_mean_mib_s",
        "max_rss_mean_mib",
        "temp_files_median",
        "temp_files_min",
        "temp_files_max",
        "disk_merge_input_fraction_median",
        "baseline_threads",
        "baseline_memory_per_thread_mib",
        "runtime_ratio_vs_baseline",
        "speedup_vs_baseline",
    ]

    with summary_csv.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()

        for key in sorted(stats_by_key):
            experiment, threads, memory_mib = key
            stats = stats_by_key[key]

            baseline_config = BASELINE_BY_EXPERIMENT.get(experiment)
            runtime_ratio: float | None = None
            speedup: float | None = None
            baseline_threads: int | None = None
            baseline_memory: int | None = None

            if baseline_config is not None:
                baseline_threads, baseline_memory = baseline_config
                baseline_stats = stats_by_key.get(
                    (experiment, baseline_threads, baseline_memory)
                )
                if baseline_stats is not None:
                    baseline_median = float(baseline_stats["wall_time_median_s"])
                    this_median = float(stats["wall_time_median_s"])
                    if baseline_median > 0 and this_median > 0:
                        runtime_ratio = this_median / baseline_median
                        speedup = baseline_median / this_median

            output = {
                "experiment": experiment,
                "threads": threads,
                "memory_per_thread_mib": memory_mib,
                "nominal_sort_memory_mib": threads * memory_mib,
                **stats,
                "baseline_threads": baseline_threads,
                "baseline_memory_per_thread_mib": baseline_memory,
                "runtime_ratio_vs_baseline": runtime_ratio,
                "speedup_vs_baseline": speedup,
            }
            writer.writerow(output)


# ============================================================
# CAMPAIGN
# ============================================================


def write_campaign_config(
    path: Path,
    *,
    mode: str,
    repeats: int,
    seed: int,
    input_bam: Path,
    cache_policy: str,
    nice_value: int | None,
) -> None:
    config = {
        "created_utc": utc_now(),
        "mode": mode,
        "repeats_per_configuration": repeats,
        "random_seed": seed,
        "input_bam": str(input_bam),
        "cache_policy": cache_policy,
        "requested_nice": nice_value,
        "memory_threads": MEMORY_THREADS,
        "memory_values_mib": MEMORY_VALUES_MIB,
        "thread_configs": THREAD_CONFIGS,
        "cliff_threads": CLIFF_THREADS,
        "cliff_low_mib": CLIFF_LOW_MIB,
        "cliff_high_mib": CLIFF_HIGH_MIB,
        "cliff_step_mib": CLIFF_STEP_MIB,
        "cliff_values_mib": CLIFF_MEMORY_VALUES_MIB,
        "baselines": BASELINE_BY_EXPERIMENT,
        "idle_cpu_threshold_percent": IDLE_CPU_THRESHOLD_PERCENT,
        "min_available_memory_mib": MIN_AVAILABLE_MEMORY_MIB,
        "settle_seconds": SETTLE_SECONDS,
    }
    path.write_text(json.dumps(config, indent=2) + "\n")


def reset_campaign_dir(campaign_dir: Path) -> None:
    if campaign_dir.exists():
        shutil.rmtree(campaign_dir)
    campaign_dir.mkdir(parents=True, exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["test", "final"], nargs="?", default="test")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_BAM)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--repeats", type=int, default=None)
    parser.add_argument("--seed", type=int, default=RANDOM_SEED)
    parser.add_argument(
        "--cache-policy",
        choices=["warm", "cold"],
        default=DEFAULT_CACHE_POLICY,
    )
    parser.add_argument(
        "--nice",
        type=int,
        default=None,
        help="Linux niceness to request (negative means higher priority and may require privilege)",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--resume", action="store_true")
    group.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    repeats = args.repeats
    if repeats is None:
        repeats = DEFAULT_TEST_REPEATS if args.mode == "test" else DEFAULT_FINAL_REPEATS
    if repeats < 1:
        parser.error("--repeats must be at least 1")

    input_bam = args.input.resolve()
    results_root = args.results_root.resolve()
    campaign_dir = results_root / args.mode

    if args.overwrite:
        reset_campaign_dir(campaign_dir)
    else:
        campaign_dir.mkdir(parents=True, exist_ok=True)

    results_csv = campaign_dir / "results.csv"
    summary_csv = campaign_dir / "summary.csv"
    system_info = campaign_dir / "system_info.txt"
    dataset_txt = campaign_dir / "dataset_info.txt"
    dataset_json = campaign_dir / "dataset_info.json"
    overhead_txt = campaign_dir / "instrumentation_overhead.txt"
    run_plan_csv = campaign_dir / "run_plan.csv"
    config_json = campaign_dir / "campaign_config.json"
    raw_dir = campaign_dir / "raw"
    raw_dir.mkdir(exist_ok=True)

    temp_root = TEMP_ROOT.resolve() / args.mode
    output_root = OUTPUT_ROOT.resolve() / args.mode
    temp_root.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)

    if results_csv.exists() and not args.resume and not args.overwrite:
        raise FileExistsError(
            f"{results_csv} already exists. Use --resume to continue it or "
            "--overwrite to start this campaign again."
        )

    preflight(input_bam)
    configure_process_priority(args.nice)
    check_disk_space(input_bam, output_root)

    save_system_info(
        system_info,
        cache_policy=args.cache_policy,
        requested_nice=args.nice,
    )
    save_dataset_info(input_bam, dataset_txt, dataset_json)
    measure_instrumentation_overhead(overhead_txt)
    write_campaign_config(
        config_json,
        mode=args.mode,
        repeats=repeats,
        seed=args.seed,
        input_bam=input_bam,
        cache_policy=args.cache_policy,
        nice_value=args.nice,
    )

    runs = build_runs(args.mode, repeats, args.seed)
    save_run_plan(run_plan_csv, runs)
    completed = read_completed_run_ids(results_csv) if args.resume else set()

    print()
    print("INF-2204 SAMtools sort benchmark")
    print("=" * 78)
    print(f"Mode: {args.mode}")
    print(f"Input: {input_bam}")
    print(f"Input size: {input_bam.stat().st_size / 1024**2:.1f} MiB")
    print(f"Planned measured runs: {len(runs)}")
    print(f"Already completed: {len(completed)}")
    print(f"Results: {results_csv}")
    print("=" * 78)

    prepare_cache(input_bam, args.cache_policy)

    for config in runs:
        if config["run_id"] in completed:
            print(f"SKIP completed: {config['run_id']}")
            continue

        result = run_one(
            config,
            input_bam=input_bam,
            raw_dir=raw_dir,
            temp_root=temp_root,
            output_root=output_root,
            cache_policy=args.cache_policy,
        )
        # Persist the run even if validation failed, so failures are part of
        # the experimental record rather than disappearing silently.
        append_result(results_csv, result)
        if result["status"] != "ok":
            raise RuntimeError(
                f"Run {result['run_id']} failed (exit={result['exit_code']}, "
                f"valid={result['output_valid']}). Inspect its raw stderr file."
            )

    create_summary(results_csv, summary_csv)

    print()
    print("=" * 78)
    print("BENCHMARK COMPLETE")
    print(f"Raw results: {results_csv}")
    print(f"Summary:     {summary_csv}")
    print(f"Dataset:     {dataset_txt}")
    print(f"System:      {system_info}")
    print(f"Overhead:    {overhead_txt}")
    print("=" * 78)


if __name__ == "__main__":
    main()
