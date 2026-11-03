#!/usr/bin/env python3
"""Build or resume indexes on an existing DuckDB variants table.

The script creates missing indexes on the variants table without
re-importing the source Parquet files. Existing indexes are skipped,
allowing the process to resume after an interruption.

Database indexes:
  idx_feature     Transcript (csq_Feature)
  idx_id          Variant identifier
  idx_symbol      Gene symbol (csq_SYMBOL)
  idx_af          Allele frequency (AF)

"""

import argparse
import logging
import re
import sys
from pathlib import Path

import duckdb

# Indexes created on the variants table to accelerate queries by:
# transcript, variant ID, gene symbol, and allele frequency.
# Each tuple contains:
#   1. Index name in DuckDB.
#   2. Column(s) indexed in the variants table.
#   3. Human-readable description used in log messages.
INDEXES = [
    ("idx_feature", "csq_Feature", "Transcripts"),
    ("idx_id", "id", "Variant IDs"),
    ("idx_symbol", "csq_SYMBOL", "Genes"),
    # ("idx_af", "AF", "Allele frequency"),
]


# Configure logging to write messages to both stdout and a log file.
def setup_logging(log_file):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            # Display progress and errors in the terminal.
            logging.StreamHandler(sys.stdout),
            # Persist logs to a file for later review.
            logging.FileHandler(log_file),
        ],
    )


# The WAL (Write-Ahead Log) file (*.wal) temporarily stores database changes
# before they are written to the main database file (*.db). It helps DuckDB
# recover changes after an unexpected interruption. A CHECKPOINT
# writes the WAL changes to the main database file and may remove the WAL.


# Print the sizes of the DuckDB database and WAL files in GB.
def report_db_sizes(db_path, label):
    """the sizes of the DuckDB database and WAL files in GB."""
    db_path = Path(db_path)
    wal_path = Path(f"{db_path}.wal")

    logging.info(f"\n{label}")

    for path in (db_path, wal_path):
        if path.exists():
            size_bytes = path.stat().st_size
            size_gb = size_bytes / (1000**3)
            logging.info(f"  {path}: {size_gb:.2f} GB ({size_bytes:,} bytes)")
        else:
            logging.info(f"  {path}: does not exist")


# Parse arguments, configure DuckDB, and create missing indexes.
def main():

    # Define the command-line interface.
    parser = argparse.ArgumentParser(
        description="Build or resume indexes on an existing DuckDB database."
    )
    parser.add_argument(
        "--db",
        type=Path,
        required=True,
        help="Existing DuckDB database file.",
    )
    parser.add_argument(
        "--memory-limit",
        default="32GB",
        help="DuckDB memory limit (default: 32GB).",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=2,
        help="Number of DuckDB threads (default: 2).",
    )
    parser.add_argument(
        "--temp-dir",
        type=Path,
        default=Path("/tmp"),
        help="Directory for temporary files.",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=Path("build_indexes.log"),
        help="Log file path.",
    )
    args = parser.parse_args()

    # Ensure the directory for the log file exists.
    args.log.parent.mkdir(parents=True, exist_ok=True)

    # Initialize logging before starting database operations.
    setup_logging(args.log)

    # Validate that the database already exists.
    if not args.db.is_file():
        logging.error("Database does not exist: %s", args.db)
        return 1

    # At least one DuckDB thread is required.
    if args.threads < 1:
        logging.error("--threads must be at least 1")
        return 1

    # Create the temporary directory if necessary.
    # DuckDB may use it for temporary data during query execution.
    args.temp_dir.mkdir(parents=True, exist_ok=True)

    con = None

    try:
        # Open the existing database.
        logging.info("Opening existing database: %s", args.db)
        con = duckdb.connect(str(args.db))

        # Validate the memory-limit format before passing it to DuckDB.
        # Accepted examples: 32GB, 40GB, 1.5GB.
        if not re.fullmatch(
            r"\d+(?:\.\d+)?\s*(?:B|KB|MB|GB|TB)",
            args.memory_limit,
            re.IGNORECASE,
        ):
            raise ValueError("Invalid --memory-limit. Example: 32GB")

        # Escape quotes before inserting values into SQL settings.
        memory_limit = args.memory_limit.replace("'", "''")
        temp_dir = str(args.temp_dir.resolve()).replace("'", "''")

        # Configure DuckDB before creating indexes.
        # The memory limit controls DuckDB's managed memory, but does
        # not impose an absolute limit on the process's total RAM use.
        con.execute(f"SET memory_limit = '{memory_limit}'")

        # Limit parallel execution to control resource consumption.
        con.execute(f"SET threads = {args.threads}")

        # Allow DuckDB to change insertion order when this reduces
        # memory usage. This is appropriate for this indexing task.
        con.execute("SET preserve_insertion_order = false")

        # Set the directory used for temporary files.
        con.execute(f"SET temp_directory = '{temp_dir}'")

        # Log the effective settings reported by DuckDB.
        logging.info(
            "Settings: memory_limit=%s, threads=%s, temp_directory=%s",
            con.execute("SELECT current_setting('memory_limit')").fetchone()[0],
            con.execute("SELECT current_setting('threads')").fetchone()[0],
            con.execute("SELECT current_setting('temp_directory')").fetchone()[0],
        )

        # Verify that the expected table exists before creating indexes.
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        if "variants" not in tables:
            raise RuntimeError("Table 'variants' does not exist.")

        # Retrieve the names of indexes currently registered in DuckDB.
        # This allows the script to skip indexes completed in a previous run.
        existing = {
            row[0]
            for row in con.execute("SELECT index_name FROM duckdb_indexes()").fetchall()
        }

        # Process indexes sequentially, one at a time.
        for index_name, columns, description in INDEXES:
            # Do not rebuild an index that is already registered.
            if index_name in existing:
                logging.info("Skipping existing index: %s", index_name)
                continue

            logging.info("Creating index %s (%s)", index_name, description)

            # Build the index on the specified column or columns.
            # If this operation fails, the exception is handled below.
            con.execute(f"CREATE INDEX {index_name} ON variants ({columns})")

            # Request a checkpoint after each successful index creation.
            # This helps persist completed work before proceeding.
            report_db_sizes(args.db, "Before CHECKPOINT")
            con.execute("CHECKPOINT")
            report_db_sizes(args.db, "After CHECKPOINT")

            # Update the in-memory set only after CREATE INDEX and
            # CHECKPOINT have both returned successfully.
            existing.add(index_name)

            logging.info("Index completed: %s", index_name)

        # Report success only after every configured index is present
        # or has been created successfully.
        logging.info("All five indexes are present.")
        return 0

    except Exception:
        # Log the exception and traceback to help diagnose failures.
        # This handler cannot run if the operating system kills the
        # process abruptly, for example because of an OOM condition.
        logging.exception("Index creation failed.")
        return 1

    finally:
        # Close the connection when possible, including after a Python
        # exception. This block cannot run if the process is killed.
        if con is not None:
            con.close()


if __name__ == "__main__":
    sys.exit(main())
