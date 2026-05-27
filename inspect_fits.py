#!/usr/bin/env python3
"""
Inspect all .fit files in a directory and print a summary table.
Usage:
    python inspect_fits.py [directory]          # defaults to ./output
    docker compose run --rm karoo-downloader python /app/inspect_fits.py /output
"""
import sys
from pathlib import Path
from datetime import timedelta

try:
    from fitparse import FitFile, FitParseError
except ImportError:
    sys.exit("fitparse not installed — run: pip install fitparse")


def fmt_duration(seconds):
    if seconds is None:
        return "-"
    return str(timedelta(seconds=int(seconds)))


def fmt_dist(meters):
    if meters is None:
        return "-"
    return f"{meters / 1000:.2f} km"


def inspect(path: Path) -> dict:
    result = {
        "file": path.name,
        "size": path.stat().st_size,
        "start_time": None,
        "total_elapsed_time": None,
        "total_distance": None,
        "records": 0,
        "errors": [],
    }
    try:
        fit = FitFile(str(path))
        for msg in fit.get_messages():
            if msg.name == "session":
                for field in msg.fields:
                    if field.name == "start_time":
                        result["start_time"] = field.value
                    elif field.name == "total_elapsed_time":
                        result["total_elapsed_time"] = field.value
                    elif field.name == "total_distance":
                        result["total_distance"] = field.value
            elif msg.name == "record":
                result["records"] += 1
    except FitParseError as e:
        result["errors"].append(str(e))
    except Exception as e:
        result["errors"].append(f"{type(e).__name__}: {e}")
    return result


def main():
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("./output")
    if not directory.exists():
        sys.exit(f"Directory not found: {directory}")

    files = sorted(directory.glob("*.fit"))
    if not files:
        print(f"No .fit files found in {directory}")
        return

    col = "{:<55} {:>8} {:<22} {:<10} {:<10} {:>8}  {}"
    header = col.format("File", "Size", "Start time", "Duration", "Distance", "Records", "Errors")
    print(header)
    print("-" * len(header))

    for path in files:
        r = inspect(path)
        name = r["file"][:54]
        size = f"{r['size']:,}"
        start = str(r["start_time"])[:22] if r["start_time"] else "-"
        duration = fmt_duration(r["total_elapsed_time"])
        distance = fmt_dist(r["total_distance"])
        records = str(r["records"])
        errors = "; ".join(r["errors"]) if r["errors"] else "ok"
        print(col.format(name, size, start, duration, distance, records, errors))


if __name__ == "__main__":
    main()
