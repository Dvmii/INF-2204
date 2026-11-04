# INF-2204 Project - SAMtools sort performance

This respository contains the benchmark scripts, experimental results, and analysis results for our INF2204 project. 

The project investigates how memory per thread and thread count affect the performance of SAMtools sort 1.19.2. Our main focus is execution time, peak memory usage, filesystem activity, and temporary-file activity. 

We performed three primary experiments, in total 720 sorting runs across 18 configurations, with 40 randomized repetitions per configuration. An additional exploratory validation experiment was conducted on a second BAM dataset.

## Files

```text
benchmark.py                 Main benchmark script
benchmark_validation.py      Validation script
data/                        Input BAM files <- not included in GitHub due to size of files
results/                     Raw benchmark results and system information
patient2/                    
dabest/                  
results_fixed.xlsx
Book1.xlsx                      
```

The main experimental data is stored in:
results/final/results.csv

The final Patient 2 validation data is stored in:
patient2/final_validation/results/validation/results.csv

The Patient 2 directory contains archived exploratory and validation campaigns:
pilot/: Initial measurements at 608, 632, 640, 648, and 768 MiB, with five repetitions each.
exploration_round1/: Four configurations from 800 to 1152 MiB, with one run each.
exploration_round2/: Five configurations from 816 to 880 MiB, with one run each.
final_validation/: Five configurations from 840 to 872 MiB, with five repetitions each.

Each campaign contains measurement data and environment information. Raw SAMtools and GNU time outputs are retained in raw/ directories.

## System and Requirements

The benchmarks were executed using the following environment:
Processor: AMD Ryzen 9 5900HX; 8 cores / 16 logical processors
Host OS: Windows 11
Execution enviroment: WSL2, Ubuntu 24.04.2 LTS
WSL2: 7.5 GiB RAM
Filesystem: ext4 on SSD
SAMtools: 1.19.2
HTSlib: 1.19
Python: 3.12.3
Resource measurment: GNU /usr/bin/time -v

## Dataset ##
Both BAM datasets orginate from the same training data availale on Zendo
BAM-file source: https://doi.org/10.5281/zenodo.892432 

dataset 1: patient1.bam
size: 613.17 MiB
alignment records: 12,386,220
SO: unsorted
Original file: patient1_input_good_outcome.bam (download: https://zenodo.org/records/892432/files/patient1_ChIP_ER_good_outcome.bam?download=1)

dataset 2: patient2.bam
size: 707.81 MiB
alignment records: 17,748,550 
SO: queryname
Original file: patient2_input_good_outcome.bam (download: https://zenodo.org/records/892432/files/patient2_input_good_outcome.bam?download=1)

Dataset 1 were prepared beforehand before running the experiments.
patient1.bam was used with recorded BAM header sort order "SO:unsorted", while patient2.bam was name-sorted before using:

```bash
samtools sort -n -@ 4 -m 768M \
  -o data/patient2.bam \
  data/patient2_ChIP_ER_good_outcome.bam
```

The input file is expected to be at:

```text
data/patient.bam
```

## Experiment Design

### Experiment 1 - Memory Scaling (RQ1)

In this experiment the number of threads is fixed at 4 while memory per thread is changed:
```text
64, 128, 256, 512, 768 and 1024 MiB
```
Repetitions: 40 per configuration (240 runs).

### Experiment 2 - Thread Scaling (RQ2)

In this experiment the thread count is changed while the planned sorting memory is kept close to 3072 MiB:
```text
1 thread  x 3072 MiB
2 threads x 1536 MiB
4 threads x  768 MiB
8 threads x  384 MiB
16 threads x 192 MiB
```
Repetitions: 40 per configuration (200 runs).

### Experiment 3 - Performance cliff (RQ3)

In this experiment the thread count is fixed at 4 and memory is tested in smaller steps around the point where temporary files disappear.
```text
600-648 MiB in 8 MiB increments
```
Repetitions: 40 per configuration (280 runs).

### Validation - Second BAM dataset

The second BAM dataset was evaluated through exploratory runs followed by a final validation campaign. 4 threads.
```text
840-872 MiB in 8 MiB increments
```
Repetitions: 5 per configuration

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
python3 benchmark_validation.py validation --repeats 5 --overwrite
```

Important: The --overwrite option deletes the existing output directory for the selected mode before running. Reproduction runs should therefore be performed in a separate working copy to avoid overwriting the archived results.

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

### Benchmark protocol
1. Randomize configuration order within each repetition block using seed 2204.
2. Warm the input file cache before the campaign.
3. Execute sync and pause for two seconds before each measured run.
4. Record execution time, peak RSS, CPU time, and SAMtools merge diagnostics.
5. Perform a basic output integrity check using samtools quickcheck.
6. Save measurement data and raw logs, then remove generated BAM output and temporary files.

## Performance Metrics
The benchmark scripts recorded the following metrics:

- **Wall-clock runtime:** Measured using Python's `time.perf_counter()` around the SAMtools execution.
- **Peak resident set size (RSS):** Collected using GNU `/usr/bin/time -v`.
- **Temporary merge-file activity:** Obtained from SAMtools' diagnostic output, reporting the number of on-disk files and in-memory blocks involved in the final merge.
- **Throughput:** Calculated as compressed input BAM size divided by wall-clock runtime.

The reported temporary merge-file count does not represent total disk I/O or all temporary files created during execution. Filesystem counters were collected, but direct disk I/O volume was not independently measured.

## Statistical analysis
The primary measurements are summarized using descriptive statistics, including the mean, median, standard deviation, and interquartile range (IQR).

The performance transition between 632 and 640 MiB was further analyzed using DABEST, version 2025.10.20. [https://github.com/ACCLAB/DABEST-python](https://github.com/ACCLAB/DABEST-python.git)
The analysis uses 40 paired repetition blocks, 20,000 bootstrap resamples, a 95% bias-corrected and accelerated (BCa) confidence interval, and a two-sided Wilcoxon signed-rank test. The relevant input data, DABEST output, reproducible commands, and estimation plot are available in the dabest/ directory.

### Figures and Excel analysis
The figures and supporting calculations used in the report are available in the Excel workbooks included in this repository. These workbooks contain visualizations of the benchmark measurements, including runtime distributions, memory scaling, thread scaling, and performance transitions.

results_fixed.xlsx contains data and figures for the main experiments.
Book1.xlsx contains the data and figures for the second BAM file.

The underlying primary measurements are available in `results/final/results.csv`. The separate DABEST analysis and its estimation plot are provided in `dabest/`.

## Reproducibility and limitations
All primary experiments were performed on the same hardware and software environment. Configuration order was randomized, and unnecessary background activity was limited during measurements.

However, the experiments were conducted under WSL2 on a Windows host. CPU-frequency changes, system temperature, filesystem caching, and background processes could not be fully controlled.

The findings describe the tested SAMtools version, workloads, and computing environment and should not be interpreted as universal performance thresholds.

The exploratory validation uses only five repetitions per configuration.

The benchmark source code, raw measurement data, configuration settings, and statistical analysis artifacts are provided to support reproducibility.
