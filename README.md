# INF-2204 Project - SAMtools sort performance

This respository contains the experimental setups and scripts for the project. The project investigates how memory per thread and thread count affect the performance of `samtools sort`.

patient1.bam -> choose SAMtools-configurations -> execute samtools sort -> measure whats happening -> save results in CSV -> delete output -> change settings -> again

## Mapping performance cliffs in SAMtools sort

Our main focus is execution time, peak memory usage, filesystem activity and temporary-file activity. The goal is to identify performance cliffs and see whether they are related to changes in temporary-file usage.

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

The unsorted BAM file that is used as input is not, as mentioned, inlcuded in git respository, as it is too large for github. 

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

TODO: UPDATE HERE

## Measurements

For every run the benchmark records:
- wall-clock execution time
- throughput
- peak memory usage (RSS)
- user and system CPU time
- filesystem inputs and outputs
- temporary files used in the final merge
- in-memory blocks used in the final merge
- temporary-file merge fraction

The resulting BAM file is checked with `samtools quickcheck` after every run. The BAM output and temporary files are then deleted.

## Running the experiments

Run all commands from the project directory.

### 1. Test the benchmark

First run the small test:

```bash
python3 benchmark.py test --overwrite
```

This runs two configurations and checks that the benchmark and measurements work correctly.

Test results are saved in:

```text
results/test/
```

### 2. Pilot experiment

Before the final experiment, run each configuration 5 times:

```bash
python3 benchmark.py final --repeats 5 --overwrite
```

This pilot is used to find the area where temporary files disappear and to choose the final range for Experiment 3.

Before starting the final campaign, keep the pilot results:

```bash
mv results/final results/pilot
```

Then update `CLIFF_EXPERIMENT` in `benchmark.py` if needed.

### 3. Final experiment

When the configurations are finalized, run:

```bash
python3 benchmark.py final --repeats 40 --overwrite
```

With the current 20 configurations this gives:

```text
20 configurations x 40 repetitions = 800 measured runs
```

The configurations are randomized within every repetition.

Final results are stored in:

```text
results/final/
```

### Continuing an interrupted experiment

If the final experiment is interrupted, do not use `--overwrite`.

Continue with:

```bash
python3 benchmark.py final --repeats 40 --resume
```

Runs that already completed successfully will be skipped.

## Results

A campaign produces:

`results.csv` contains the measurements used for analysis.

`raw/` contains the raw output from SAMtools and GNU time for each run.

`dataset_info.txt` describes the input BAM file.

`system_info.txt` contains information about the machine and software
environment.

## Experimental conditions

The experiments should be run on the same machine and software environment. During the final campaign, avoid other heavy workloads such as gaming, downloads or compilation.

The benchmark uses a warm input-file cache and randomizes the configuration order to reduce systematic effects from run order and background variation.

## Validation

After the primary experiment, selected configurations before, around and after the observed performance cliff will also be tested on a second BAM file. And a small subset may also be tested on another computer if time allows.

