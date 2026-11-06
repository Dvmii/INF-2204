import os

def second_convert(t):
    seconds = 0
    for part in t.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds

def find_lines(path):
    with open(path) as f:
        for line in f:
            line = line.strip()
            value = line.rsplit(" ", 1)[-1]
            if line.startswith("User time"):
                user = float(value)
            elif line.startswith("System time"):
                system = float(value)
            elif line.startswith("Elapsed (wall clock)"):
                elapsed = second_convert(value)
    
    return user, system, elapsed

def process_txt(raw_files, min_ratio):
    file_count = 0

    if not os.path.exists(raw_files):
        print("ERROR: NO RAW FILES")
        return


    for root, dirs, files in os.walk(raw_files):
        for filename in files:
            if not filename.lower().endswith(".txt"):
                continue
            file_count += 1
            full_path = os.path.join(root, filename)

            user, system, elapsed = find_lines(full_path)
            if None in (user, system, elapsed):
                print(f"ERROR: no value in {full_path}")
                continue

            cpu = user + system
            ratio = elapsed / cpu if cpu > 0 else float("inf")

            if ratio >= min_ratio:
                print(f"{full_path}: CPU={cpu:.2f}s, elapsed={elapsed:.2f}s, "
                      f"forhold={ratio:.1f}x")

process_txt("raw", 2)