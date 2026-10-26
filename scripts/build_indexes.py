#!/usr/bin/env python3
"""Build or resume indexes on an existing DuckDB variants table."""

import argparse
import logging
import sys
from pathlib import Path

import duckdb

INDEXES = [
    ("idx_chrom_pos", "chrom, pos", "Coordinates"),
    ("idx_feature", "csq_Feature", "Transcripts"),
    ("idx_id", "id", "Variant IDs"),
    ("idx_symbol", "csq_SYMBOL", "Genes"),
    ("idx_af", "AF", "Allele frequency"),
]


def setup_logging(log_file):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file),
        ],
    )


def main():
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
        default=Path("/home/ubuntu/duckdb_tmp"),
        help="Directory for temporary files.",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=Path("build_indexes.log"),
        help="Log file path.",
    )
    args = parser.parse_args()

    args.log.parent.mkdir(parents=True, exist_ok=True)
    setup_logging(args.log)

    if not args.db.is_file():
        logging.error("Database does not exist: %s", args.db)
        return 1

    if args.threads < 1:
        logging.error("--threads must be at least 1")
        return 1

    args.temp_dir.mkdir(parents=True, exist_ok=True)

    con = None
    try:
        logging.info("Opening existing database: %s", args.db)
        con = duckdb.connect(str(args.db))

        # Configure DuckDB before creating indexes.
        # Validate the memory-limit string as a simple numeric size.
        import re

        if not re.fullmatch(
            r"\d+(?:\.\d+)?\s*(?:B|KB|MB|GB|TB)",
            args.memory_limit,
            re.IGNORECASE,
        ):
            raise ValueError("Invalid --memory-limit. Example: 32GB")

        memory_limit = args.memory_limit.replace("'", "''")
        temp_dir = str(args.temp_dir.resolve()).replace("'", "''")

        con.execute(f"SET memory_limit = '{memory_limit}'")
        con.execute(f"SET threads = {args.threads}")
        con.execute("SET preserve_insertion_order = false")
        con.execute(f"SET temp_directory = '{temp_dir}'")

        logging.info(
            "Settings: memory_limit=%s, threads=%s, temp_directory=%s",
            con.execute("SELECT current_setting('memory_limit')").fetchone()[0],
            con.execute("SELECT current_setting('threads')").fetchone()[0],
            con.execute("SELECT current_setting('temp_directory')").fetchone()[0],
        )

        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        if "variants" not in tables:
            raise RuntimeError("Table 'variants' does not exist.")

        # Skip indexes already committed to the database.
        existing = {
            row[0]
            for row in con.execute("SELECT index_name FROM duckdb_indexes()").fetchall()
        }

        for index_name, columns, description in INDEXES:
            if index_name in existing:
                logging.info("Skipping existing index: %s", index_name)
                continue

            logging.info("Creating index %s (%s)", index_name, description)

            con.execute(f"CREATE INDEX {index_name} ON variants ({columns})")

            # Persist completed work before proceeding.
            con.execute("CHECKPOINT")

            existing.add(index_name)
            logging.info("Index completed: %s", index_name)

        logging.info("All five indexes are present.")
        return 0

    except Exception:
        logging.exception("Index creation failed.")
        return 1

    finally:
        if con is not None:
            con.close()


if __name__ == "__main__":
    sys.exit(main())
