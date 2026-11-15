"""
Sensitivity check for instrumation overhead introduced by /usr/bin/time -v.

The measurement procedure follows warm_input_cache() and run_benchmark() in benchmark.py

We read the input once, sync and wait before each run, and time subprocess.run() with time.perf_counter().
Validation, logging and output cleanup are outside the timed interval.
Within each pair, only the GNU time wrapper is changed.

"""

import csv
import random
import subprocess
import time
from pathlib import Path

BAM = Path("data/patient1.bam")
number_of_threads = 4
memory_values = [632, 640]
number_of_pairs = 10
random_seed = 2204

if not BAM.is_file():
    raise FileNotFoundError(f"Input BAM not found: {BAM}")
if not Path("/usr/bin/time").is_file():
    raise FileNotFoundError("GNU time not found in /usr/bin/time")

subprocess.run(["samtools", "quickcheck", "-v", str(BAM)], check=True)

result_dir = Path("results") / time.strftime("overhead_%Y%m%d_%H%M%S")
result_dir.mkdir(parents=True)
output_bam = result_dir / "output.bam"
temp_prefix = result_dir / "tmp"


def measure(command_to_run, run_id):
    subprocess.run(["sync"], check=True)
    time.sleep(2)

    start = time.perf_counter()
    process = subprocess.run(
        command_to_run,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    runtime = time.perf_counter() - start

    # Saving the diagnostics before checking 
    (result_dir / f"{run_id}.txt").write_text(
        process.stderr, encoding="utf-8"
    )
    process.check_returncode()
    subprocess.run(
        ["samtools", "quickcheck", "-v", str(output_bam)], check=True
    )
    output_bam.unlink()
    return runtime


# the same input-cache preparation as benchmark.py
print("Warming input file cache...", flush=True)
with BAM.open("rb") as file:
    while file.read(8 * 1024 * 1024):
        pass

rng = random.Random(random_seed)
results_file = result_dir / "results.csv"

with results_file.open("w", newline="", encoding="utf-8") as file:
    writer = csv.writer(file)
    writer.writerow([
        "run_id", "threads", "memory_mib", "pair", "position",
        "condition", "runtime_seconds",
    ])

    for memory_per_thread in memory_values:
        command = [
            "samtools", "sort",
            "-@", str(number_of_threads),
            "-m", f"{memory_per_thread}M",
            "-T", str(temp_prefix),
            "-o", str(output_bam),
            str(BAM),
        ]
        command_with_time = ["/usr/bin/time", "-v"] + command

        # GNU time runs first in five pairs and second in five.
        time_first = [pair % 2 == 0 for pair in range(number_of_pairs)]
        rng.shuffle(time_first)

        for pair, use_time_first in enumerate(time_first, start=1):
            order = [
                ("without_time", command),
                ("with_time", command_with_time),
            ]
            if use_time_first:
                order.reverse()

            for position, (condition, command_to_run) in enumerate(order, start=1):
                run_id = (
                    f"t{number_of_threads}_m{memory_per_thread}_"
                    f"pair{pair:02d}_{condition}"
                )
                print(f"Running {run_id}...", flush=True)
                runtime = measure(command_to_run, run_id)
                writer.writerow([
                    run_id, number_of_threads, memory_per_thread,
                    pair, position, condition, runtime,
                ])
                file.flush()
                print(f"Runtime: {runtime:.2f} s", flush=True)

print(f"Finished. Results: {results_file}")