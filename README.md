# INF-2204 Project - SAMtools sort performance

This respository contains the experimental setups and scripts for the project. 

patient1.bam -> choose SAMtools-configurations -> execute samtools sort -> measure whats happening -> save results in CSV -> delete output -> change settings -> again

## Respository structure

INF-2204/
- benchmark.py
- data/
- - patient1.bam <- local file
- plots/
- results/
- - results.csv
- - system_into.txt
- benchmark_outputs
- benchmark_tmp
- .gitignore
- README.md

## Dataset ##

The unsorted BAM file that is used as input is not inlcuded in git respository, as it is too large for github. 

File size: 614 MiB
number of alignment records: 12386220

## Requirements ##
- Python 3
SAMtools
GNU time

## Benchmark ##
The benchmark contains three experiments (as of now). 

Experiment 1:
- The number of threads is fixed at 4 while the memory per thread varies. 
- We test -m = 64M, 128M, 256M, 512M, 768M, and 1024M. 

Experiment 2:
- The mumber of threads is varied while the memory budget is constant.
- The value of -m is adjusted according to the number of threads:
    -@ 1, -m 3072M
    -@ 2, -m 1536M
    -@ 4, -m 768M
    -@ 8, -m 384M
    -@ 16, -m 192M

Experiment 3:
- The number of threads is fixed at 4, while -m is varied in a smaller range aroung the point where temprarry files disappear.
- The exact -m values will be selected after calibration on the machine we will run the experiments on.  

## RUN ##
You need to input the BAM file in data/patient1.bam.
Use TEST_MODE = True to verify the benchmark works correctly.
Run with: python3 benchmark.py
Use TEST_MODE = False for the final experiment.

You will get this output: results.csv, system_info.txt.


