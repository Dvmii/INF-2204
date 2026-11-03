# INF-2204 Project - SAMtools sort performance

This respository contains the experimental setups and scripts for our INF2204 project. The project investigates how memory per thread and thread count affect the performance of `samtools sort`.

## Mapping performance cliffs in SAMtools sort

Our main focus is execution time, peak memory usage, filesystem activity, and temporary-file activity. The goal is to identify performance cliffs and see whether they are related to changes in temporary-file usage.

## Files

```text
benchmark.py       Benchmark script
data/              Input BAM files
results/           Raw benchmark results and system information
plots/             Figures created from the results
```

## Requirements

The experiments are run in Ubuntu/WSL2 and require:

- Python 3
- SAMtools
- GNU `/usr/bin/time`

The primary input file is expected to be at:

```text
data/patient1.bam
```

## Dataset ##

The unsorted BAM file that is used as input is not, as mentioned, inlcuded in git respository, as it is too large for github. (The second BAM file used for valdation is also from the same source).

File size: 614 MiB
number of alignment records: 12386220
BAM-file source: https://doi.org/10.5281/zenodo.892432 

## Experiments

### Experiment 1 - Memory per thread

In this experiment the number of threads is fixed at 4 while memory per thread is changed:

```text
64, 128, 256, 512, 768 and 1024 MiB
```

### Experiment 2 - Thread count

In this experiment the thread count is changed while the planned sorting memory is kept close to 3072 MiB:

```text
1 thread  x 3072 MiB
2 threads x 1536 MiB
4 threads x  768 MiB
8 threads x  384 MiB
16 threads x 192 MiB
```

### Experiment 3 - Performance cliff

in this experiment the thread count is fixed at 4 and memory is tested in smaller steps around the point where temporary files disappear.

```text
600-648 MiB in 8 MiB increments
```

## Running the experiments

Run all commands from the project directory. Place input files in data/.

```bash
# Test the benchmark:
python3 benchmark.py test --overwrite

# Run the primary experiments:
python3 benchmark.py final --repeats 40 --overwrite

# Resume an interrupted experiment
python3 benchmark.py final --repeats 40 --resume

# Run validation on second BAM file:
python3 benchmark.py validation --repeats 5 --overwrite
```

The configurations are randomized within every repetition.

Final results are stored in:

```text
results/
```

and includes:

`results.csv` contains the measurements used for analysis.

`raw/` contains the raw output from SAMtools and GNU time for each run.

`dataset_info.txt` describes the input BAM file.

`system_info.txt` contains information about the machine and software
environment.
