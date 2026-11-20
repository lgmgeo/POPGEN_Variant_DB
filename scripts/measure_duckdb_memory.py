#!/usr/bin/env python3

"""Measure process memory and query execution times for a DuckDB database."""

import argparse
import os
import time
from pathlib import Path

import duckdb
import psutil

# Get the current Python process to monitor its resident memory usage.
process = psutil.Process(os.getpid())


def report_memory(label):
    """Print the current process RSS (Resident Set Size) in MiB."""
    rss_mb = process.memory_info().rss / (1024**2)
    print(f"{label:<40} RAM: {rss_mb:,.1f} MiB")


def main():

    parser = argparse.ArgumentParser(
        description="Measure process memory and query execution times for a DuckDB database."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        required=True,
        metavar="DIR",
        help="Dataset directory containing the duckdb/ subdirectory.",
    )
    args = parser.parse_args()

    dataset_dir = args.dataset
    dataset_name = dataset_dir.name
    db_path = dataset_dir / "duckdb" / f"{dataset_name}.db"

    if not db_path.is_file():
        parser.error(f"DuckDB database not found: {db_path}")

    # 1. Measure memory before opening the database.
    report_memory("Before opening the database")

    # 2. Open the database in read-only mode.
    con = duckdb.connect(str(db_path), read_only=True)
    report_memory("After opening the database")

    # 3. Define representative queries to execute sequentially.
    # Adapt these queries to match the actual columns and indexes
    # available in the database.
    queries = [
        (
            "Search by gene",
            """
            SELECT COUNT(*)
            FROM variants
            WHERE csq_SYMBOL = 'DDX11L1'
            """,
        ),
        (
            "Search by genomic coordinates",
            """
            SELECT COUNT(*)
            FROM variants
            WHERE chrom = 'chr1'
              AND pos BETWEEN 1000000 AND 2000000
            """,
        ),
        (
            "Search for non-null variant IDs",
            """
            SELECT COUNT(*)
            FROM variants
            WHERE id IS NOT NULL
            """,
        ),
        (
            "Search by allele frequency",
            """
            SELECT COUNT(*)
            FROM variants
            WHERE AF < 0.01
            """,
        ),
        (
            "Count all table rows",
            """
            SELECT COUNT(*)
            FROM variants
            """,
        ),
    ]

    try:
        # 4. Execute each query and measure memory and execution time.
        for i, (label, sql) in enumerate(queries, start=1):
            print(f"\n--- Query {i}: {label} ---")

            # Record memory immediately before executing the query.
            report_memory("Before query")

            start_time = time.perf_counter()
            result = con.execute(sql).fetchall()
            elapsed = time.perf_counter() - start_time

            # Record memory after the query has completed.
            report_memory("After query")

            print(f"Execution time: {elapsed:.3f} seconds")
            print(f"Result: {result[0][0]}")

    finally:
        # 5. Always close the connection, even if a query fails.
        con.close()

    # 6. Check memory after closing the database connection.
    report_memory("After closing the database")


if __name__ == "__main__":
    main()
