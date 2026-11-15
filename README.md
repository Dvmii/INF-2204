# INF-2204 Project - SAMtools sort performance

**INF-2204**
**Damian Matulis and Benedicte Jansen**

This respository contains the benchmark scripts, experimental results, and analysis results for our INF2204 project. 

The project investigates how memory per thread and thread count affect the performance of SAMtools sort 1.19.2. Our main focus is execution time, peak memory usage, filesystem activity, and temporary-file activity. 

We performed three primary experiments, in total 720 sorting runs across 18 configurations, with 40 randomized repetitions per configuration. An additional exploratory validation experiment was conducted on a second BAM dataset.

## Files

| File or directory | Contents |
|---|---|
| [benchmark.py](benchmark.py) | Main benchmark script. |
| [benchmark_validation.py](benchmark_validation.py) | Script with the final Patient 2 validation configurations. |
| [instrumentation_overhead.py](instrumentation_overhead.py) | Paired comparison with and without GNU time. |
| [results/final/](results/final/) | Primary campaign: 720 measurements, raw logs, and dataset and system information. |
| [results/test/](results/test/) | Initial benchmark test measurements. |
| [patient2/](patient2/) | Archived pilot, exploratory, and final validation campaigns. |
| [results_main_campaign.xlsx](excel%20files/results_main_campaign.xlsx) | Main analysis workbook and figures. |
| [check_716_observations.xlsx](excel%20files/check_716_observations.xlsx) | Supplementary sensitivity analysis using 716 observations. |
| [results_validation_run.xlsx](excel%20files/results_validation_run.xlsx) | Analysis workbook for the second BAM dataset. |
| [dabest/](dabest/) | Paired analysis inputs, instructions, recorded DABEST output, and an estimation plot. |
| [final_monitor (1).csv](monitoring/final_monitor%20%281%29.csv) | Windows host monitoring during the primary campaign. |
| [overheadmonitor20261115_135012.csv](monitoring/overheadmonitor20261115_135012.csv) | Host monitoring recorded during the supplementary work. |
| [results/output_check_without_BAMs/](results/output_check_without_BAMs/) | Output correctness statistics, alignment counts, and SAMtools version information. |

The input BAM files are not included because of their large size. The benchmark scripts expect them in a local `data/` directory. How to locate the usen BAM files are explained under Datasets.

The main experimental data are in [results/final/results.csv](results/final/results.csv). The final Patient 2 validation data are in [patient2/final_validation/results/validation/results.csv](patient2/final_validation/results/validation/results.csv).

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
Detailed information is available in: [results/final/system_info.txt](results/final/system_info.txt).

The benchmark and overhead script uses the Python standard library, SAMtools, GNU time at /usr/bin/time, and the Linux sync command must be available.

The DABEST analysis uses pandas and DABEST version 2025.10.20. Installation steps from [DABEST project](https://github.com/ACCLAB/DABEST-python). 

## Datasets
Both BAM datasets orginate from the same training data availale on Zendo
BAM-file source: https://doi.org/10.5281/zenodo.892432 

dataset 1: patient1.bam

size: 613.17 MiB
alignment records: 12,386,220
SO: unsorted

Original file: patient1_ChIP_ER_good_outcome.bam 
download:https://zenodo.org/records/892432/files/patient1_ChIP_ER_good_outcome.bam?download=1

dataset 2: patient2.bam

size: 707.81 MiB
alignment records: 17,748,550 
SO: queryname

Original file: patient2_ChIP_ER_good_outcome.bam
download: https://zenodo.org/records/892432/files/patient2_ChIP_ER_good_outcome.bam?download=1

Dataset 1 were prepared beforehand before running the experiments.
patient1.bam was used with recorded BAM header sort order "SO:unsorted", while patient2.bam was name-sorted before using:

```bash
samtools sort -n -@ 4 -m 768M \
  -o data/patient2.bam \
  data/patient2_ChIP_ER_good_outcome.bam
```

The input file is expected to be at:

```text
data/patient1.bam
data/patient2.bam
```
The dataset metadata is recorded in [results/final/dataset_info.txt](results/final/dataset_info.txt) and [the final validation dataset information](patient2/final_validation/results/validation/dataset_info.txt).

## Experiment Design

### Experiment 1 - Memory Scaling (RQ1)

In this experiment the number of threads is fixed at 4 while memory per thread is changed:
```text
64, 128, 256, 512, 768 and 1024 MiB
```
Repetitions: 40 per configuration (total 240 runs).

### Experiment 2 - Thread Scaling (RQ2)

In this experiment the thread count is changed while the planned sorting memory is kept close to 3072 MiB:
```text
1 thread  x 3072 MiB
2 threads x 1536 MiB
4 threads x  768 MiB
8 threads x  384 MiB
16 threads x 192 MiB
```
Repetitions: 40 per configuration (total 200 runs).

### Experiment 3 - Performance cliff (RQ3)

In this experiment the thread count is fixed at 4 and memory is tested in smaller steps around the point where temporary files disappear.
```text
600-648 MiB in 8 MiB increments
```
Repetitions: 40 per configuration (total 280 runs).

### Validation - Second BAM dataset

The second BAM dataset was evaluated through exploratory runs followed by a final validation campaign. 4 threads.
```text
840-872 MiB in 8 MiB increments
```
Repetitions: 5 per configuration (total 25 runs)

The pilot, explorations and final run data can be found at:
[pilot](patient2/pilot/)
[exploration_round1](patient2/exploration_round1/)
[exploration_round2](patient2/exploration_round2/) 
[final_validation](patient2/final_validation/) 

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

### Generated files
| Command or mode | Output directory |
|---|---|
| `benchmark.py test` | `results/test/` |
| `benchmark.py final` | `results/final/` |
| `benchmark_validation.py validation` | `results/validation/` |

Each campaign directory contains:

- `results.csv`: per-run measurements.
- `raw/`: combined SAMtools and GNU time diagnostic output for each run.
- `dataset_info.txt`: input dataset metadata.
- `system_info.txt`: recorded hardware and software information.

The patient 2 campaigns are archived under `patient2/`. 

### Benchmark protocol
1. Randomize configuration order within each repetition block using seed 2204 + repreat.
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

## Environmental Monitoring
During the primary benchmark campaign on October 27, 2026, Windows host-level performance counters were collected using PowerShell `typeperf`.

The monitoring recorded CPU utilization, processor performance and frequency, disk utilization, disk throughput, and average disk queue length at approximately one-second intervals.

The monitoring was started before the benchmark campaign and continued beyond the final benchmark run. The resulting timestamped measurements are stored in `monitoring/final_monitor(1).csv`.

The benchmark measurements also contain execution timestamps, allowing host-level activity to be examined alongside individual benchmark runs.

The computer was connected to external power, Windows performance mode was used, and unnecessary applications and background workloads were limited during the experiments.

**Monitoring does not imply complete environmental isolation.** Background processes, CPU-frequency changes, filesystem caching, and other host-level interference could not be fully controlled. The performance counters were used to characterize the experimental environment and investigate possible sources of runtime variability.

## Statistical analysis
The primary measurements are summarized using descriptive statistics, including the mean, median, standard deviation, and interquartile range (IQR).

The performance transition between 632 and 640 MiB was further analyzed using DABEST, version 2025.10.20. [https://github.com/ACCLAB/DABEST-python](https://github.com/ACCLAB/DABEST-python.git)
The analysis uses 40 paired repetition blocks, 20,000 bootstrap resamples, a 95% bias-corrected and accelerated (BCa) confidence interval, and a two-sided Wilcoxon signed-rank test. The relevant input data, DABEST output, reproducible commands, and estimation plot are available in the dabest/ directory.

| Comparison | Input data |
|---|---|
| 632 versus 640 MiB per thread, with four threads | [dabest_cliff_632_vs_640.csv](dabest/dabest_cliff_632_vs_640.csv) |
| 8 versus 16 threads, with the nominal memory budget fixed at 3072 MiB | [dabest_8_vs_16_paired.csv](dabest/dabest_8_vs_16_paired.csv) |

Each of the two analyses pairs observations by repetition block and uses 40 pairs, 20,000 bootstrap resamples, a 95% bias-corrected and accelerated (BCa) confidence interval for the mean difference, and random seed 2204.

Two-sided Wilcoxon signed-rank tests provide a supplementary comparison. Their interpretation is limited by uncertainty about the symmetry of the paired difference distributions. Interpretation primarily concerns mean effect estimates and bootstrap confidence intervals. These comparisons are exploratory.

The paired mean difference is calculated as the second condition minus the first. A negative runtime difference therefore favors the second condition. The interval describes uncertainty in the mean difference, rather than the spread of individual runtimes or a difference between medians.

### Figures and Excel analysis
The figures and supporting calculations used in the report are available in the Excel workbooks included in this repository. These workbooks contain visualizations of the benchmark measurements, including runtime distributions, memory scaling, thread scaling, and performance transitions.

results_fixed.xlsx contains data and figures for the main experiments.
Book1.xlsx contains the data and figures for the second BAM file.

The underlying primary measurements are available, see the file-overview. 

## Timing discrepancies and sensitivity analysis

The main analysis retains all 720 primary measurements. Four runs had large differences between GNU time elapsed duration and Python-measured runtime:

| Run ID | Python runtime (s) | GNU time elapsed (s) | Difference (s) | Host-monitoring timestamp gap (s) |
|---|---:|---:|---:|---:|
| `cliff_rep02_t4_m608` | 14.1469 | 118.58 | 104.43 | 105.32 |
| `threads_rep08_t2_m1536` | 17.2782 | 240.08 | 222.80 | 223.63 |
| `memory_rep10_t4_m128` | 29.6117 | 567.96 | 538.35 | 539.33 |
| `threads_rep30_t1_m3072` | 30.7038 | 964.58 | 933.88 | 934.82 |

Each run coincided with a gap in the host-monitoring timestamps. The gap duration matched the GNU time-Python timing difference to within one second. Yet, this does not establish the cause of either the monitoring gap or the timing discrepancy. 

The Python runtime values are recorded in [results/final/results.csv](results/final/results.csv); GNU time elapsed durations are retained in the corresponding [raw logs](results/final/raw/). Monitoring gaps can be inspected in the host-monitoring CSV.

A separate sensitivity check excluding these four observations is documented in [check_716_observations.xlsx](excel%20files/check_716_observations.xlsx). This check supplements the main analysis and does not replace the 720-run source data.

## Instrumention overhead control
[instrumentation_overhead.py](instrumentation_overhead.py) compares the same sort command with and without the GNU `/usr/bin/time -v` wrapper. Both conditions are measured using Python `time.perf_counter()`.

To run the control:
```bash
python3 instrumentation_overhead.py
```
The script creates a new timestamped directory named `results/overhead_YYYYMMDD_HHMMSS/`, containing `results.csv` and per-run diagnostic logs. Its CSV records the run identifier, thread and memory settings, pair number, within-pair position, condition, and runtime.

A related host-monitoring trace is available in [monitoring/overheadmonitor20261115_135012.csv](monitoring/overheadmonitor20261115_135012.csv). 
And the files in [results/overhead_20261115_135052](results/overhead_20261115_135052). 

Also see dabest/dabest_overheas.csv and the dabest/dabest_instruction.txt for the analysis on the results.

## Output correctness check
Three addition output files were examined using samtools stats and samtools view -c, seperatly from the timed primary campaign.

| Thread setting | Memory per thread (MiB) | SAMtools `is sorted` | Alignment records |
|---:|---:|---:|---:|
| 4 | 632 | 1 | 12,386,220 |
| 4 | 640 | 1 | 12,386,220 |
| 16 | 192 | 1 | 12,386,220 |

SAMtools reported coordinate-sorted output for all three files. Each output count matched that of the primary input count of 12386220 alignment records.

The statistics, count files, and SAMtools version information are retained in [results/output_check_without_BAMs/](results/output_check_without_BAMs/). The generated BAM files are not included (too large).

This does not change that the primary campaigns integrity check remains as just a samtools quickcheck.

## Reproducibility and limitations
The archived scripts, parameter settings, measurements, diagnostic logs, system and dataset metadata, analysis workbooks, and supplementary correctness outputs document the experimental workflow. The benchmark source code, raw measurement data, configuration settings, and statistical analysis artifacts are provided to support reproducibility.

All primary experiments were performed on the same hardware and software environment. Configuration order was randomized, and unnecessary background activity was limited during measurements. However, the experiments were conducted under WSL2 on a Windows host. CPU-frequency changes, system temperature, filesystem caching, and background processes could not be fully controlled.

The findings describe the tested SAMtools version, workloads, and computing environment and should not be interpreted as universal performance thresholds.Throughput was derived from the input BAM size divided by wall-clock execution time.

The exploratory validation uses only five repetitions per configuration. Patient 2 also differs in dataset properties and initial sort order. It does not isolate the effects of dataset characteristics, and the observed transition should not be interpreted as universal.

The overhead control addresses the GNU time wrapper at two settings. Host-monitoring overhead remains a separate limitation.
