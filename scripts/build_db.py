#!/usr/bin/env python3
"""
DuckDB Database Builder for Variant Datasets.

This script converts a partitioned Parquet dataset into a single indexed
DuckDB database file to enable high-performance analytical queries.

Key Features:
------------
- Automated indexing for: Coordinates, Transcripts (csq_Feature),
  IDs, Genes (csq_SYMBOL), and Frequencies (AF).
- Namespace-aware indexing (handles csq_ prefixes).
- Timestamped logging for audit trails.
- Robust error handling and schema enforcement.
"""

import argparse
import logging
import sys
from pathlib import Path

import duckdb

# =============================================================================
# Configuration & Logging
# =============================================================================


def setup_logging():
    """Configures logging to the console only."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


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
            logging.info(f"Starting database build at {self.db_path}")

            # Determine search pattern
            # If user points to 'variants/', we look inside.
            # If user points to 'variants/chrom=chr1', we use the directory directly.
            if (self.input_dir / "variants").exists():
                search_pattern = f"{self.input_dir}/variants/chrom=*/*.parquet"
            elif any(self.input_dir.glob("chrom=*/*.parquet")):
                search_pattern = f"{self.input_dir}/chrom=*/*.parquet"
            else:
                search_pattern = f"{self.input_dir}/**/*.parquet"

            logging.info(f"Source data pattern: {search_pattern}")

            # Connect to DuckDB (creates the file if it doesn't exist)
            self.conn = duckdb.connect(str(self.db_path))

            # 1. Create the table from Parquet
            logging.info("Step 1/3: Importing Parquet files into DuckDB table...")
            # hive_partitioning=True is critical to recognize the 'chrom' column from folder names
            self.conn.execute(f"""
                CREATE TABLE variants AS 
                SELECT * FROM read_parquet('{search_pattern}', hive_partitioning=true) 
                WHERE chrom IS NOT NULL;
            """)

            row_count = self.conn.execute("SELECT count(*) FROM variants").fetchone()[0]
            logging.info(f"Successfully imported {row_count} rows.")

            # 2. Create Indexes
            # IMPORTANT: We must use the exact column names produced by the converter
            # (csq_ for CSQ fields)
            logging.info("Step 2/3: Building performance indexes...")

            # A. Search by coordinates (Mandatory columns)
            logging.info(" -> Creating index: idx_chrom_pos (Coordinates)")
            self.conn.execute("CREATE INDEX idx_chrom_pos ON variants (chrom, pos);")

            # B. Search by Transcript (CSQ field)
            logging.info(" -> Creating index: idx_feature (Transcripts)")
            self.conn.execute("CREATE INDEX idx_feature ON variants (csq_Feature);")

            # C. Search by Variant ID (Mandatory column)
            logging.info(" -> Creating index: idx_id (Variant IDs)")
            self.conn.execute("CREATE INDEX idx_id ON variants (id);")

            # D. Search by Gene (CSQ field)
            logging.info(" -> Creating index: idx_symbol (Genes)")
            self.conn.execute("CREATE INDEX idx_symbol ON variants (csq_SYMBOL);")

            # E. Search by Allele Frequency (INFO field)
            logging.info(" -> Creating index: idx_af (Frequency)")
            self.conn.execute("CREATE INDEX idx_af ON variants (AF);")
           
            # 3. Finalize
            logging.info("Step 3/3: Finalizing and flushing to disk...")
            self.conn.execute("CHECKPOINT;")

            logging.info("================================================")
            logging.info("DATABASE BUILD COMPLETED SUCCESSFULLY.")
            logging.info(f"Database file: {self.db_path}")
            logging.info("================================================")

        except Exception as e:
            logging.error(f"DATABASE BUILD FAILED: {str(e)}")
            raise
        finally:
            if self.conn:
                self.conn.close()


# =============================================================================
# Main Execution
# =============================================================================



def main():
    parser = argparse.ArgumentParser(
        prog="build_db.py",
        description=(
            "Build an indexed DuckDB database from a partitioned "
            "Parquet variant dataset."
        ),
        epilog="""\
Examples:
    python build_db.py --input_dir data/processed/snvindel.200variants/variants \\
                       --db_output db/snvindel.200variants.db

Input structure:
  data/processed/snvindel.200variants/
  └── variants/
      ├── chrom=chr1/
      │   └── part-000001.parquet
      ├── chrom=chr2/
      │   └── part-000001.parquet
      └── ...

Database indexes:
  idx_chrom_pos   Chromosome and position
  idx_feature     Transcript (csq_Feature)
  idx_id          Variant identifier
  idx_symbol      Gene symbol (csq_SYMBOL)
  idx_af          Allele frequency (AF)

Notes:
  - The input Parquet dataset must contain the required columns.
  - The output database (--db_output) is created from the input Parquet files.
  - An existing output database file is never overwritten.

""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    required = parser.add_argument_group("required arguments")
    required.add_argument(
        "--input_dir",
        "-i",
        type=Path,
        required=True,
        metavar="DIR",
        help="Directory containing the partitioned Parquet dataset, organized \
            into chromosome-specific subdirectories (e.g. chrom=chr1, chrom=chr2, ...).",
    )
    required.add_argument(
        "--db_output",
        "-o",
        type=Path,
        required=True,
        metavar="FILE",
        help="Path to the output DuckDB database file.",
    )

    args = parser.parse_args()

    # Initialize console logging
    setup_logging()

    # Validate input directory
    if not args.input_dir.exists():
        logging.error(f"Input directory does not exist: {args.input_dir}")
        sys.exit(1)

    if not args.input_dir.is_dir():
        logging.error(f"Input path is not a directory: {args.input_dir}")
        sys.exit(1)

    # Refuse to overwrite an existing database.
    if args.db_output.exists():
        logging.error(
            f"Output database already exists: {args.db_output}. "
            "Please remove it or choose another output path."
        )
        sys.exit(1)

    # Run the database builder
    builder = DatabaseBuilder(args.input_dir, args.db_output)

    try:
        builder.build()
    except Exception:
        sys.exit(1)



if __name__ == "__main__":
    main()
