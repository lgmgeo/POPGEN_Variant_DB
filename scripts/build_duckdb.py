#!/usr/bin/env python3
"""
DuckDB Database Builder for Variant Datasets.

This script converts a partitioned Parquet dataset into a single
DuckDB database file (not indexed) to enable high-performance analytical queries.

"""

import argparse
import logging
import sys
from pathlib import Path

import duckdb

# =============================================================================
# Configuration & Logging
# =============================================================================


def setup_logging(log_level, db_output):
    """Configure logging to both a dated log file and the console."""
    from datetime import datetime

    dataset_dir = Path(db_output).parent.parent
    log_dir = dataset_dir / "logs"

    try:
        log_dir.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y%m%d")
        log_file = log_dir / f"{Path(__file__).stem}_{date_str}.log"

        logging.basicConfig(
            level=log_level,
            format="%(asctime)s %(levelname)s %(message)s",
            handlers=[
                logging.FileHandler(log_file, mode="w", encoding="utf-8"),
                logging.StreamHandler(sys.stdout),
            ],
            force=True,
        )

    except OSError as exc:
        raise SystemExit(
            f"ERROR: Cannot create or write to log directory '{log_dir}': {exc}"
        )

    logging.info("Log file: %s", log_file)


# =============================================================================
# Core Builder Class
# =============================================================================


class DatabaseBuilder:
    def __init__(self, input_dir: Path, db_path: Path):
        self.input_dir = input_dir
        self.db_path = db_path
        self.conn = None

    def build(self):
        """Orchestrates the DB creation process."""
        try:
            logging.info("Starting database build at %s", self.db_path)

            # Locate partitioned Parquet files
            variants_dir = self.input_dir / "variants"

            if not variants_dir.is_dir():
                raise FileNotFoundError(
                    f"Variants directory does not exist: {variants_dir}"
                )

            search_pattern = str(variants_dir / "chrom=*" / "*.parquet")

            parquet_files = list(variants_dir.glob("chrom=*/*.parquet"))

            if not parquet_files:
                raise FileNotFoundError(
                    f"No Parquet files found in '{variants_dir}/chrom=*/'."
                )

            logging.info("Found %d Parquet files.", len(parquet_files))
            logging.info("Source data pattern: %s", search_pattern)

            # Connect to DuckDB (creates the file if it doesn't exist)
            self.conn = duckdb.connect(str(self.db_path))

            # Create the table from Parquet
            logging.info("Importing Parquet files into DuckDB table...")
            # hive_partitioning=True is critical to recognize the 'chrom' column from folder names
            self.conn.execute(
                """
                CREATE TABLE variants AS
                SELECT *
                FROM read_parquet(?, hive_partitioning=true)
                WHERE chrom IS NOT NULL;
                """,
                [search_pattern],
            )

            row_count = self.conn.execute("SELECT count(*) FROM variants").fetchone()[0]
            logging.info("Successfully imported %d rows.", row_count)

            # Finalize
            logging.info("Finalizing and flushing to disk...")
            self.conn.execute("CHECKPOINT;")

            logging.info("================================================")
            logging.info("DATABASE BUILD COMPLETED SUCCESSFULLY.")
            logging.info("Database created without performance indexes.")
            logging.info("Database file: %s", self.db_path)
            logging.info("Run build_indexes.py to create the indexes.")
            logging.info("================================================")

        except Exception:
            logging.exception("DATABASE BUILD FAILED")
            raise

        finally:
            if self.conn:
                self.conn.close()


# =============================================================================
# Main Execution
# =============================================================================


def main():

    parser = argparse.ArgumentParser(
        prog="build_duckdb.py",
        description=(
            "Build a DuckDB database (not indexed) from a partitioned "
            "Parquet variant dataset."
        ),
        epilog="""\
Examples:
    python build_duckdb.py --dataset data/datasets/snvindel.200variants

Input structure:
    data/datasets/snvindel.200variants/
    ├── parquet/
    │   └── variants/
    │       └── chrom=chr1/
    │           └── part-000001.parquet
    ├── duckdb/
    │   └── snvindel.200variants.db
    └── logs/
        └── build_duckdb_<TIMESTAMP>.log

Notes:
  - The input Parquet dataset must contain the required columns.
  - The output database is created in the dataset's duckdb/ directory.
  - An existing output database file is never overwritten.

""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--dataset",
        type=Path,
        required=True,
        metavar="DIR",
        help="Dataset directory containing the parquet/ subdirectory.",
    )
    logging_group = parser.add_argument_group("logging options")
    logging_group.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        metavar="LEVEL",
        help="Set the logging verbosity.",
    )

    args = parser.parse_args()

    dataset_dir = args.dataset
    input_dir = dataset_dir / "parquet"

    dataset_name = dataset_dir.name
    db_dir = dataset_dir / "duckdb"
    db_output = db_dir / f"{dataset_name}.db"

    # Validate dataset directory
    if not dataset_dir.exists() or not dataset_dir.is_dir():
        parser.error(f"Dataset directory does not exist: {dataset_dir}")

    # Validate input Parquet directory
    if not input_dir.exists() or not input_dir.is_dir():
        parser.error(f"Parquet directory does not exist: {input_dir}")

    # Refuse to overwrite an existing database.
    if db_output.exists():
        parser.error(
            f"Database already exists: {db_output}. "
            "Please remove it or choose another output path."
        )

    # Create the duckdb directory if it does not exist.
    db_dir.mkdir(parents=True, exist_ok=True)

    # Initialize console logging
    setup_logging(args.log_level, db_output)

    # Run the database builder
    builder = DatabaseBuilder(input_dir, db_output)

    try:
        builder.build()
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
