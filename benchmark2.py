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