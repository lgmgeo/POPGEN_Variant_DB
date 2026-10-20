#!/usr/bin/env python3
"""
High-performance Variant to Single-Table Parquet converter.

This script flattens an annotated VCF/BCF into a single, denormalized Parquet dataset.
Each row in the output represents exactly one VEP annotation (one row per transcript per variant).

Key Features:
------------
- All INFO and CSQ fields of 1 transcript are in one row.
- Streaming: Processes records one-by-one to handle massive files (30GB+) in low RAM.
- Partitioning: Hive-style partitioning by chromosome.
- Robust Typing: Maps VCF INFO (Flag, Integer, Float, String) to strict Arrow types.
- Optimized Storage: Uses ZSTD compression and Dictionary encoding for high compression.
- Fast Indexing: Built-in DuckDB-based indexing for ID, Gene, and Feature searches.

Main outputs:
-------------
data/
    variants/
        chrom=<value>/
            part-nnnnnn.parquet (Single denormalized table)

id_index.parquet, gene_index.parquet, etc. (Lookup tables)
"""

import argparse
import json
import logging
import re
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pysam

# =============================================================================
# Configuration & Constants
# =============================================================================

DEFAULT_CHUNK_SIZE = 50_000
DEFAULT_MAX_FILE_SIZE = 1024**3
DEFAULT_ROW_GROUP_SIZE = 100_000
CHECKPOINT_FILENAME = "checkpoint.json"

CSQ_FIELDS_TO_KEEP = [
    "Consequence",
    "IMPACT",
    "SYMBOL",
    "Gene",
    "Feature_type",
    "Feature",
    "BIOTYPE",
    "EXON",
    "INTRON",
    "CDS_position",
    "Protein_position",
    "Amino_acids",
    "Codons",
]

# =============================================================================
# Variant Type Logic
# =============================================================================


def get_variant_type(ref, alt):
    """
    Determines biological variant type.
    Treats '.' as length 0 to distinguish between INS and DEL.
    """
    r = "" if (ref == "." or ref is None) else ref
    a = "" if (alt == "." or alt is None) else alt
    ref_len, alt_len = len(r), len(a)

    if ref_len == 0 and alt_len == 0:
        return "UNKNOWN"
    if ref_len == 1 and alt_len == 1:
        return "SNV"
    if ref_len == alt_len:
        return "MNV"
    if ref_len < alt_len:
        return "INS"
    if ref_len > alt_len:
        return "DEL"
    return "OTHER"


# =============================================================================
# Type Conversion & Normalization
# =============================================================================


def _arrow_type_for_info(info_def):
    """Maps VCF INFO types to strict PyArrow types."""
    vcf_type = str(info_def.type).upper()

    if "INTEGER" in vcf_type:
        return pa.int64()
    elif "FLOAT" in vcf_type:
        return pa.float64()
    elif "FLAG" in vcf_type:
        return pa.bool_()
    else:
        return pa.string()


def _is_frequency_field(name, info_def):
    """Checks if an INFO field is a frequency (for rounding)."""
    if info_def.type != "Float":
        return False
    norm = re.sub(r"[^A-Za-z0-9]+", "_", name).upper()
    return any(x in norm for x in ["AF", "FREQ", "FREQUENCY"])


def _convert_info_scalar(value, info_def, field_name):
    """Converts VCF INFO values to Python/Arrow types with handling for missing values."""
    if value is None or value == "" or value == "." or value == "None":
        return False if info_def.type == "Flag" else None

    if info_def.type == "Flag":
        return str(value).lower() not in {"", ".", "none", "false", "0"}

    if info_def.type == "Integer":
        return int(value)

    if info_def.type == "Float":
        num = float(value)
        return round(num, 4) if _is_frequency_field(field_name, info_def) else num

    return str(value)


def normalize_info_value(value, info_definition, field_name):
    """Handles scalar vs array conversion for VCF INFO fields."""
    if info_definition.type == "Flag":
        return _convert_info_scalar(value, info_definition, field_name)

    is_scalar = info_definition.number in (1, "A")

    if is_scalar:
        if isinstance(value, (tuple, list)):
            value = value[0] if value else None
        return _convert_info_scalar(value, info_definition, field_name)
    else:
        if value is None or value == "" or value == "." or value == "None":
            return None
        if not isinstance(value, (tuple, list)):
            value = (value,)
        return [
            _convert_info_scalar(item, info_definition, field_name) for item in value
        ]


# =============================================================================
# CSQ Parsing
# =============================================================================


def get_csq_field_indices(header):
    """
    Parses VEP CSQ header to get indices of required fields.

    Returns a dictionary mapping field names to their indices in the CSQ annotation.
    e.g., {"Gene": 1, "Impact": 3}

    Raises ValueError if any required field is missing.
    """

    if "CSQ" not in header.info:
        raise ValueError("The input VCF/BCF has no CSQ INFO field.")

    description = header.info["CSQ"].description
    marker = "Format: "
    if marker not in description:
        raise ValueError("The CSQ INFO description does not contain 'Format: ' marker.")

    fields = description.split(marker, 1)[1].split("|")
    csq_field_indices = {field_name: index for index, field_name in enumerate(fields)}

    missing = [field for field in CSQ_FIELDS_TO_KEEP if field not in csq_field_indices]
    if missing:
        raise ValueError(f"Missing CSQ fields in VCF header: {', '.join(missing)}")

    return csq_field_indices


def parse_csq_annotations(csq_value, target_indices):
    """
    Version ultra-robuste pour extraire les champs CSQ.
    """
    # 1. Si le champ est absent ou vide
    if csq_value is None or csq_value == "" or csq_value == ".":
        return []

    # 2. Normalisation : pysam peut renvoyer un tuple (si multi-transcrits)
    # ou une string (si un seul transcrit). On veut toujours une liste d'annotations.
    if isinstance(csq_value, tuple):
        all_annotations = csq_value
    elif isinstance(csq_value, str):
        all_annotations = (csq_value,)
    else:
        # Cas rare où l'objet est d'un autre type
        all_annotations = (str(csq_value),)

    rows = []
    for ann_string in all_annotations:
        # On split la chaîne de l'annotation par le pipe
        fields = ann_string.split("|")

        row = {}
        for i, target_idx in enumerate(target_indices):
            field_name = CSQ_FIELDS_TO_KEEP[i]

            # On vérifie que l'index existe bien dans la ligne splitée
            if target_idx < len(fields):
                val = fields[target_idx]
                # On ne stocke pas les chaînes vides comme des valeurs valides
                row[field_name] = val if val != "" else None
            else:
                row[field_name] = None

        # On n'ajoute la ligne que si elle contient au moins une info non-nulle
        if any(v is not None for v in row.values()):
            rows.append(row)

    return rows


# =============================================================================
# Schema Construction
# =============================================================================


def build_combined_schema(vcf) -> pa.schema:
    """
    Builds a robust, flattened schema containing both Variant metadata and CSQ fields.
    Ensures no column name collisions and enforces strict typing based on VCF header.
    """
    # 1. Define core variant metadata fields (Mandatory fields)
    # Using a dictionary to track fields and prevent duplicate names
    schema_dict = {
        "variant_key": pa.field("variant_key", pa.string(), nullable=False),
        "chrom": pa.field("chrom", pa.string(), nullable=False),
        "pos": pa.field("pos", pa.int64(), nullable=False),
        "id": pa.field("id", pa.string(), nullable=True),
        "ref": pa.field("ref", pa.string(), nullable=False),
        "alt": pa.field("alt", pa.string(), nullable=False),
        "qual": pa.field("qual", pa.float64(), nullable=True),
        "filter": pa.field("filter", pa.string(), nullable=True),
        "variant_type": pa.field("variant_type", pa.string(), nullable=False),
    }


    # 2. Dynamically add INFO fields from the VCF/BCF header.
    info_names = set(vcf.header.info)
    # CSQ is handled separately, so it must not be added here.
    info_names.discard("CSQ")

    # Check for name collisions with columns already defined in the schema.
    existing_names = set(schema_dict)
    collisions = sorted(info_names & existing_names)

    # Stop immediately if an INFO field would overwrite an existing column.
    if collisions:
        raise ValueError(
            "INFO field name collision(s) with existing schema columns: "
            + ", ".join(collisions)
        )

    # Add each INFO field to the schema using its original name and
    # the Arrow data type inferred from its VCF/BCF header definition.
    for name in sorted(info_names):
        info_def = vcf.header.info[name]
        schema_dict[name] = pa.field(
            name, _arrow_type_for_info(info_def), nullable=True
        )

    # 3. Add flattened CSQ annotation fields
    # Prefixing CSQ fields with 'csq_' to ensure a unique namespace
    for field in CSQ_FIELDS_TO_KEEP:
        column_name = f"csq_{field}"

        # Safety check: if a CSQ field name conflicts with an existing column
        if column_name in schema_dict:
            column_name = f"csq_alt_{field}"

        schema_dict[column_name] = pa.field(column_name, pa.string(), nullable=True)

    # 4. Construct the final PyArrow schema from the dictionary values
    return pa.schema(list(schema_dict.values()))


# =============================================================================
# Processing Engine
# =============================================================================


def build_rows_flattened(record, annotations, info_definitions):
    """
    Version corrigée pour s'assurer que les données de base (chrom, pos, etc.)
    sont bien transmises avec les annotations CSQ.
    """
    ref = record.ref
    alt = record.alts[0] if record.alts else "."
    variant_key = f"{record.chrom}:{record.pos}:{ref}:{alt}"
    v_type = get_variant_type(ref, alt)

    # On prépare le dictionnaire de base (toutes les infos du variant)
    base_info = {
        "variant_key": variant_key,
        "chrom": record.chrom,
        "pos": record.pos,
        "id": record.id,
        "ref": ref,
        "alt": alt,
        "qual": record.qual,
        "filter": (";".join(record.filter.keys()) if record.filter.keys() else None),
        "variant_type": v_type,
    }

    # On ajoute les champs INFO (non-CSQ)
    for name, info_def in info_definitions.items():
        column_name = (
            f"info_{name}"
            if name
            in {
                "variant_key",
                "chrom",
                "pos",
                "id",
                "ref",
                "alt",
                "qual",
                "filter",
                "variant_type",
            }
            else name
        )

        base_info[column_name] = normalize_info_value(
            record.info.get(name),
            info_def,
            name,
        )

    # On "aplatit" : pour chaque annotation CSQ, on crée une ligne complète
    flattened_rows = []
    for ann in annotations:
        # On fusionne le dictionnaire de base avec le dictionnaire de l'annotation
        new_row = base_info.copy()
        new_row.update(
            {f"csq_{field_name}": value for field_name, value in ann.items()}
        )
        flattened_rows.append(new_row)

    return flattened_rows



class DatasetWriter:
    """Manage buffered Parquet writes partitioned by chromosome."""

    def __init__(self, output_dir, schema, chunk_size, max_file_size, row_group_size):
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero.")
        if max_file_size <= 0:
            raise ValueError("max_file_size must be greater than zero.")
        if row_group_size <= 0:
            raise ValueError("row_group_size must be greater than zero.")

        self.output_dir = Path(output_dir)
        self.schema = schema
        self.chunk_size = chunk_size
        self.max_file_size = max_file_size
        self.row_group_size = row_group_size

        self.writers = {}
        self.current_files = {}
        self.file_counters = {}
        self.buffers = {}

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._initialize_counters_from_existing_files()

    def _initialize_counters_from_existing_files(self):
        """Initialize counters from existing Parquet files."""
        variants_dir = self.output_dir / "variants"

        for chrom_dir in variants_dir.glob("chrom=*"):
            if not chrom_dir.is_dir():
                continue

            chrom = chrom_dir.name.split("=", 1)[1]
            part_numbers = []

            for path in chrom_dir.glob("part-*.parquet"):
                match = re.fullmatch(r"part-(\d+)", path.stem)
                if match:
                    part_numbers.append(int(match.group(1)))

            if part_numbers:
                self.file_counters[chrom] = max(part_numbers) + 1

    def _open_writer(self, chrom):
        """Open a new Parquet file for a chromosome."""
        chrom_dir = self.output_dir / "variants" / f"chrom={chrom}"
        chrom_dir.mkdir(parents=True, exist_ok=True)

        part_number = self.file_counters.get(chrom, 0)
        path = chrom_dir / f"part-{part_number:06d}.parquet"

        # Never silently overwrite an existing Parquet file.
        if path.exists():
            raise FileExistsError(
                f"Refusing to overwrite existing Parquet file: {path}"
            )

        parquet_writer = pq.ParquetWriter(
            path,
            self.schema,
            compression="zstd",
            use_dictionary=True,
        )

        self.writers[chrom] = parquet_writer
        self.current_files[chrom] = path

    def _close_writer(self, chrom):
        """Close the active writer and advance to the next part number."""
        parquet_writer = self.writers.get(chrom)
        if parquet_writer is None:
            return

        # Update internal state only after a successful close.
        parquet_writer.close()
        self.writers.pop(chrom)
        self.current_files.pop(chrom, None)
        self.file_counters[chrom] = self.file_counters.get(chrom, 0) + 1

    def _write_rows(self, chrom, rows):
        """Write one batch and rotate the file when its size limit is reached."""
        if not rows:
            return

        if chrom not in self.writers:
            self._open_writer(chrom)

        table = pa.Table.from_pylist(rows, schema=self.schema)
        self.writers[chrom].write_table(
            table,
            row_group_size=self.row_group_size,
        )

        if self.current_files[chrom].stat().st_size >= self.max_file_size:
            self._close_writer(chrom)

    def add_rows(self, rows):
        """Buffer rows by chromosome and write complete chunks."""
        if not rows:
            return

        by_chrom = {}
        for row in rows:
            by_chrom.setdefault(row["chrom"], []).append(row)

        for chrom, chrom_rows in by_chrom.items():
            buffer = self.buffers.setdefault(chrom, [])
            buffer.extend(chrom_rows)

            while len(buffer) >= self.chunk_size:
                chunk = buffer[:self.chunk_size]
                self._write_rows(chrom, chunk)
                del buffer[:self.chunk_size]

    def flush(self):
        """Write all remaining buffered rows without closing active writers."""
        for chrom in list(self.buffers):
            buffer = self.buffers[chrom]
            if buffer:
                self._write_rows(chrom, buffer)
                buffer.clear()

    def finish_chromosome(self, chrom):
        """Write remaining rows and close the chromosome before checkpointing."""
        buffer = self.buffers.get(chrom, [])

        if buffer:
            self._write_rows(chrom, buffer)
            buffer.clear()

        self._close_writer(chrom)

    def close(self):
        """Flush all buffers and close every active writer."""
        self.flush()

        for chrom in list(self.writers):
            self._close_writer(chrom)

    def get_counters(self):
        """Return the next unused part number for each chromosome."""
        return dict(self.file_counters)



# =============================================================================
# Checkpoint Management
# =============================================================================


def _load_checkpoint_and_sync(output_dir):
    """
    Loads the checkpoint JSON and synchronizes it with the actual files on disk.
    If a chromosome's directory or files have been deleted, its entry is removed
    from the checkpoint to allow a fresh re-processing.
    """
    path = Path(output_dir) / CHECKPOINT_FILENAME
    checkpoint = {}

    # 1. Load the existing JSON if it exists
    if path.exists():
        try:
            checkpoint = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logging.error(f"Failed to read checkpoint: {e}. Starting fresh.")
            return {}

    # 2. Synchronization process
    chroms_to_remove = []
    for chrom in checkpoint.keys():
        chrom_dir = Path(output_dir) / "variants" / f"chrom={chrom}"

        # Check if the directory exists
        if not chrom_dir.exists():
            logging.warning(
                f"Checkpoint entry for '{chrom}' found, but directory is missing. Resetting."
            )
            chroms_to_remove.append(chrom)
            continue

        # Check if there are actually any parquet files inside
        # This prevents checkpointing chromosomes that might have been emptied
        if not list(chrom_dir.glob("part-*.parquet")):
            logging.warning(
                f"No parquet files found for '{chrom}'. Resetting checkpoint for this chrom."
            )
            chroms_to_remove.append(chrom)

    # 3. Clean up the checkpoint dictionary
    if chroms_to_remove:
        for chrom in chroms_to_remove:
            del checkpoint[chrom]

        logging.info(
            f"Synchronized checkpoint. Removed stale entries: {chroms_to_remove}"
        )

        # Save the cleaned version immediately to disk
        try:
            path.write_text(json.dumps(checkpoint, indent=2) + "\n", encoding="utf-8")
        except Exception as e:
            logging.error(f"Failed to update synchronized checkpoint: {e}")

    return checkpoint


def _save_checkpoint(output_dir, progress_dict):
    """Saves the current progress to a JSON file."""
    target = Path(output_dir) / CHECKPOINT_FILENAME
    tmp = target.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(progress_dict, indent=2) + "\n", encoding="utf-8")
        tmp.replace(target)
    except Exception as e:
        logging.error(f"Failed to write checkpoint: {e}")


# =============================================================================
# Main Execution Flow
# =============================================================================


def convert_vcf(
    input_file,
    output_dir,
    chunk_size,
    max_file_size,
    row_group_size,
    chromosome_filter=None,
    resume=False,
    overwrite=False,
):
    input_path = Path(input_file)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # 1. Handle Overwrite logic
    #    - If a specific chromosome is selected, remove only its existing Parquet
    #      partition and invalidate its checkpoint entry so it can be reprocessed.
    #    - Preserve checkpoint entries for all other chromosomes to allow
    #      --resume to skip chromosomes that have already been processed.
    #    - Remove lookup indexes and summary files, as they may become outdated
    #      after overwriting a chromosome partition.
    #    - If no chromosome filter is specified, remove the entire output directory
    #      and recreate it, including all partitions, indexes, summaries, and checkpoints.
    if overwrite:
        derived_files = [
            "id_index.parquet",
            "gene_index.parquet",
            "feature_index.parquet",
            "Impact.txt",
            "consequences.txt",
            "features.tsv",
            "genes.tsv",
        ]

        if chromosome_filter:
            # Remove only the selected chromosome partition
            target_chrom_dir = output_path / "variants" / f"chrom={chromosome_filter}"

            if target_chrom_dir.exists():
                logging.info(
                    "Overwriting existing partition: %s",
                    target_chrom_dir,
                )
                shutil.rmtree(target_chrom_dir)

            # Update checkpoint: invalidate only the overwritten chromosome
            checkpoint_path = output_path / CHECKPOINT_FILENAME

            if checkpoint_path.exists():
                try:
                    checkpoint_data = json.loads(
                        checkpoint_path.read_text(encoding="utf-8")
                    )
                    if chromosome_filter in checkpoint_data:
                        del checkpoint_data[chromosome_filter]
                        _save_checkpoint(output_path, checkpoint_data)
                        logging.info(
                            "Removed '%s' from checkpoint.",
                            chromosome_filter,
                        )
                except (OSError, json.JSONDecodeError) as e:
                    raise RuntimeError(
                        f"Cannot safely update checkpoint {checkpoint_path}"
                    ) from e

            # Remove indexes and summaries that may now be outdated
            for filename in derived_files:
                file_path = output_path / filename
                if file_path.exists():
                    logging.info("Removing outdated file: %s", file_path)
                    file_path.unlink()

        else:
            # Full overwrite: remove everything, including checkpoint
            logging.info(
                "Overwriting entire output directory: %s",
                output_path,
            )
            if output_path.exists():
                shutil.rmtree(output_path)
            output_path.mkdir(parents=True, exist_ok=True)

    # 2. Load Checkpoint
    checkpoint_data = {}
    if resume:
        checkpoint_data = _load_checkpoint_and_sync(output_path)
        logging.info(f"Resuming from checkpoint: {checkpoint_data}")

    # 3. Init VCF and Schema
    vcf = pysam.VariantFile(str(input_path))

    # Pre-calculate CSQ field indices for efficient access (e.g., {"Gene": 1, "Impact": 3})
    csq_field_indices = get_csq_field_indices(vcf.header)

    # LIST of field names to extract:
    # CSQ_FIELDS_TO_KEEP = ["Gene", "Impact"]
    # Pre-calculate the actual indices to avoid dictionary lookups in the inner loop
    # Transform: {"Gene": 1, "Impact": 3} -> [1, 3]
    csq_target_indices = [csq_field_indices[f] for f in CSQ_FIELDS_TO_KEEP]

    combined_schema = build_combined_schema(vcf)
    info_definitions = {
        name: vcf.header.info[name] for name in vcf.header.info if name != "CSQ"
    }

    # 4. Init Writer
    writer = DatasetWriter(
        output_path,
        combined_schema,
        chunk_size,
        max_file_size,
        row_group_size,
    )

    # Sync writer counters with checkpoint if resuming
    if resume:
        for chrom, next_part in checkpoint_data.items():
            writer.file_counters[chrom] = next_part

    # 5. Chromosome Iteration
    chroms_to_process = (
        [chromosome_filter] if chromosome_filter else list(vcf.header.contigs)
    )

    try:
        for chrom in chroms_to_process:
            # Skip if chrom is already in checkpoint (it's done)
            if resume and chrom in checkpoint_data:
                logging.info(f"Skipping already processed chromosome: {chrom}")
                continue

            # Track if any variants were actually written for this chromosome
            variants_found_in_chrom = False
            total_variants_read = 0
            LOG_INTERVAL = 150_000

            try:
                for record in vcf.fetch(chrom):
                    total_variants_read += 1

                    # Log progress every LOG_INTERVAL variants
                    if total_variants_read % LOG_INTERVAL == 0:
                        logging.info(
                            "Processed %d variants in chromosome %s",
                            total_variants_read,
                            chrom,
                        )

                    csq_val = record.info.get("CSQ")
                    annotations = parse_csq_annotations(csq_val, csq_target_indices)
                    rows = build_rows_flattened(record, annotations, info_definitions)

                    if rows:
                        writer.add_rows(rows)
                        if not variants_found_in_chrom:
                            variants_found_in_chrom = True
                            logging.info("Starting chromosome: %s", chrom)

            except ValueError as e:
                if "fetch requires an index" in str(e):
                    logging.error(
                        "\n" + "=" * 60 + "\n"
                        " ERROR: Missing Index File\n"
                        " " + "=" * 60 + "\n"
                        " The VCF/BCF file is not indexed.\n"
                        " To use chromosome-specific filtering (--chromosome), \n"
                        " you MUST provide a .tbi or .csi index file.\n"
                        " Use 'bcftools index <file>' to create it.\n"
                        " " + "=" * 60
                    )
                    return
                else:
                    raise e

            # Finalize the chromosome before saving its checkpoint.
            writer.finish_chromosome(chrom)

            if variants_found_in_chrom:
                checkpoint_data[chrom] = writer.file_counters.get(chrom, 0)
                _save_checkpoint(output_path, checkpoint_data)
                logging.info("Finished chromosome: %s (Checkpoint updated)", chrom)

    finally:
        writer.close()
        vcf.close()


def build_lookup_indexes(output_dir):
    """
    Builds lightweight lookup Parquet files to accelerate filtering by
    ID, Gene, or Feature.

    Note: Column names are prefixed with 'csq_' because of the flattening logic.
    """
    from pathlib import Path

    import duckdb

    output_path = Path(output_dir)

    # 1. Check if data exists before starting DuckDB
    variants_root = output_path / "variants"
    if not variants_root.exists() or not list(variants_root.glob("chrom=*/*.parquet")):
        logging.error(
            f"No Parquet files found in {variants_root}. Cannot build indexes."
        )
        return

    logging.info(
        f"Building lookup indexes using pattern: {variants_root}/chrom=*/*.parquet"
    )

    con = duckdb.connect()
    # Use absolute path for the glob pattern to avoid DuckDB path resolution issues
    main_table_pattern = f"'{variants_root.absolute()}/chrom=*/*.parquet'"

    try:
        # Task 1: ID Index (Uses 'id' column)
        logging.info("Creating id_index.parquet...")
        con.execute(f"""
            COPY (
                SELECT id, variant_key 
                FROM read_parquet({main_table_pattern})
                WHERE id IS NOT NULL AND id != ''
            ) TO '{output_path.absolute()}/id_index.parquet' (FORMAT 'PARQUET')
        """)

        # Task 2: Gene Index (Uses 'csq_SYMBOL' column)
        logging.info("Creating gene_index.parquet...")
        con.execute(f"""
            COPY (
                SELECT csq_SYMBOL as gene, variant_key 
                FROM read_parquet({main_table_pattern})
                WHERE csq_SYMBOL IS NOT NULL AND csq_SYMBOL != ''
            ) TO '{output_path.absolute()}/gene_index.parquet' (FORMAT 'PARQUET')
        """)

        # Task 3: Feature Index (Uses 'csq_Feature' column)
        logging.info("Creating feature_index.parquet...")
        con.execute(f"""
            COPY (
                SELECT csq_Feature, variant_key 
                FROM read_parquet({main_table_pattern})
                WHERE csq_Feature IS NOT NULL AND csq_Feature != ''
            ) TO '{output_path.absolute()}/feature_index.parquet' (FORMAT 'PARQUET')
        """)

    except Exception as e:
        logging.error(f"Error during index building: {e}")
        raise
    finally:
        con.close()


def build_text_summary_files(output_dir):
    """
    Generates summary text files (Impact, Consequences, Features, Genes)
    based on the generated Parquet dataset.
    """
    import logging
    from pathlib import Path

    import duckdb

    logging.info(
        "Building text summary files (Impact, Consequences, Features, Genes)..."
    )
    con = duckdb.connect()

    variants_root = Path(output_dir) / "variants"

    # CORRECTION ICI :
    # 1. On utilise un glob simple sur tous les fichiers .parquet
    # 2. On retire hive_partitioning=true car 'chrom' est déjà une colonne dans vos fichiers
    # 3. On utilise le chemin absolu pour éviter toute ambiguïté
    main_table_pattern = str(variants_root.absolute() / "chrom=*/*.parquet")

    try:
        # On vérifie d'abord si on trouve des fichiers pour éviter un crash DuckDB
        if not list(variants_root.glob("chrom=*/*.parquet")):
            logging.error(
                f"No parquet files found in {variants_root}. Cannot build summary files."
            )
            return

        # 1. Impact.txt
        logging.info("Generating Impact.txt...")
        con.execute(f"""
            COPY (
                SELECT DISTINCT csq_IMPACT 
                FROM read_parquet('{main_table_pattern}')
                WHERE csq_IMPACT IS NOT NULL AND csq_IMPACT != ''
                ORDER BY csq_IMPACT
            ) TO '{Path(output_dir).absolute()}/Impact.txt' (FORMAT 'CSV', DELIMITER '\n', HEADER FALSE)
        """)

        # 2. consequences.txt
        logging.info("Generating consequences.txt...")
        con.execute(f"""
            COPY (
                SELECT DISTINCT csq_Consequence 
                FROM read_parquet('{main_table_pattern}')
                WHERE csq_Consequence IS NOT NULL AND csq_Consequence != ''
                ORDER BY csq_Consequence
            ) TO '{Path(output_dir).absolute()}/consequences.txt' (FORMAT 'CSV', DELIMITER '\n', HEADER FALSE)
        """)

        # 3. features.tsv
        logging.info("Generating features.tsv...")
        con.execute(f"""
            COPY (
                SELECT DISTINCT csq_Feature, chrom, csq_Gene
                FROM read_parquet('{main_table_pattern}')
                WHERE csq_Feature IS NOT NULL AND csq_Feature != ''
                ORDER BY csq_Feature
            ) TO '{Path(output_dir).absolute()}/features.tsv' (FORMAT 'CSV', DELIMITER '\t', HEADER FALSE)
        """)

        # 4. genes.tsv
        logging.info("Generating genes.tsv...")
        con.execute(f"""
            COPY (
                SELECT csq_Gene, list_aggregate(array_agg(DISTINCT chrom), 'string_agg', ',') as chroms
                FROM read_parquet('{main_table_pattern}')
                WHERE csq_Gene IS NOT NULL AND csq_Gene != ''
                GROUP BY csq_Gene
                ORDER BY csq_Gene
            ) TO '{Path(output_dir).absolute()}/genes.tsv' (FORMAT 'CSV', DELIMITER '\t', HEADER FALSE)
        """)

        logging.info("All summary files created successfully.")

    except Exception as e:
        logging.error(f"Failed to build text summary files: {e}")
        raise
    finally:
        con.close()


def main():

    parser = argparse.ArgumentParser(
        prog="variant2parquet__snvindel.py",
        description=(
            "Convert an annotated VCF/BCF file into a partitioned Parquet "
            "dataset, with one row per VEP annotation. "
            "Alternatively, build lookup indexes and text summaries "
            "from an existing dataset."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  1. Convert an entire VCF/BCF file:
     python3 %(prog)s --input tests/synthetic_catalogue_snvindel.200variants.vcf.gz --output data/processed/snvindel.200variants

  2. Convert a single chromosome from scratch:
     python3 %(prog)s --input tests/synthetic_catalogue_snvindel.200variants.vcf.gz --output data/processed/snvindel.200variants \\
              --chromosome chr1

  3. Resume an interrupted conversion:
     python3 %(prog)s --input tests/synthetic_catalogue_snvindel.200variants.vcf.gz --output data/processed/snvindel.200variants \\
              --resume

  4. Restart the conversion from scratch:
     python3 %(prog)s --input tests/synthetic_catalogue_snvindel.200variants.vcf.gz --output data/processed/snvindel.200variants \\
              --overwrite

  5. Build lookup indexes and text summaries:
     python3 %(prog)s --output data/processed/snvindel.200variants --build-indexes

  6. Customize processing parameters:
     python3 %(prog)s --input tests/synthetic_catalogue_snvindel.200variants.vcf.gz --output data/processed/snvindel.200variants \\
              --chunk-size 25000 --max-file-size 536870912 \\
              --row-group-size 50000 --log-level DEBUG

Input formats:
  Supports VCF and BCF files readable by pysam.
  Chromosome-specific processing requires an indexed input file.

Output structure:
  OUTPUT/
  ├── variants/
  │   ├── chrom=chr1/
  │   │   └── part-000000.parquet
  │   └── chrom=chr2/
  │       └── part-000000.parquet
  ├── checkpoint.json
  ├── id_index.parquet
  ├── gene_index.parquet
  └── feature_index.parquet

Notes:
  - Each output row represents one VEP annotation.
  - CSQ fields are stored with the 'csq_' prefix.
  - --chromosome reprocesses the selected chromosome from scratch.
  - --chromosome and --resume cannot be used together.
  - --overwrite and --resume cannot be used together.
  - Without --chromosome, --overwrite replaces the entire output directory.
  - --build-indexes does not require --input.
""",
    )

    # Input and output paths
    io_group = parser.add_argument_group("input and output")
    io_group.add_argument(
        "--input",
        "-i",
        type=Path,
        metavar="FILE",
        help="Input annotated VCF/BCF file (required for conversion).",
    )
    io_group.add_argument(
        "--output",
        "-o",
        type=Path,
        required=True,
        metavar="DIR",
        help="Output directory for the Parquet dataset, indexes, and summaries.",
    )

    # Processing options
    processing_group = parser.add_argument_group("processing options")
    processing_group.add_argument(
        "--chromosome",
        "-c",
        metavar="CHROM",
        help="Process only the specified chromosome, reprocessing it from scratch.",
    )
    processing_group.add_argument(
        "--resume",
        action="store_true",
        help="Resume conversion using checkpoint.json and skip completed chromosomes.",
    )
    processing_group.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Overwrite existing output. Replaces the entire output directory, "
            "unless --chromosome is specified."
        ),
    )
    processing_group.add_argument(
        "--build-indexes",
        action="store_true",
        help="Build lookup indexes and text summaries from existing Parquet files.",
    )

    # Performance options
    performance_group = parser.add_argument_group("performance options")
    performance_group.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        metavar="N",
        help="Number of rows buffered per chromosome before writing a batch.",
    )
    performance_group.add_argument(
        "--max-file-size",
        type=int,
        default=DEFAULT_MAX_FILE_SIZE,
        metavar="BYTES",
        help="Approximate maximum Parquet file size (in bytes) before rotating to a new file.",
    )
    performance_group.add_argument(
        "--row-group-size",
        type=int,
        default=DEFAULT_ROW_GROUP_SIZE,
        metavar="N",
        help="Maximum number of rows per Parquet row group.",
    )

    # Logging options
    logging_group = parser.add_argument_group("logging options")
    logging_group.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        metavar="LEVEL",
        help="Set the logging verbosity.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level, format="%(asctime)s %(levelname)s %(message)s"
    )

    # --chromosome and --resume are mutually exclusive:
    # Specifying a chromosome triggers its reprocessing from scratch.
    if args.chromosome and args.resume:
        parser.error(
            "--chromosome and --resume cannot be used together. "
            "When --chromosome is specified, the selected chromosome "
            "is reprocessed from scratch. Use --chromosome without "
            "--resume."
        )

    # --overwrite and --resume are mutually exclusive
    if args.overwrite and args.resume:
        parser.error(
            "--overwrite and --resume cannot be used together."
        )

    # When a chromosome is specified, always overwrite that chromosome.
    # Resume mode is not applicable to a targeted chromosome conversion.
    if args.chromosome:
        args.overwrite = True
        args.resume = False

    if args.build_indexes:
        # If building indexes, we don't need the input file argument
        build_lookup_indexes(args.output)
        build_text_summary_files(args.output)
    else:
        # If NOT building indexes, we ARE in parquet conversion mode, so --input is mandatory
        if args.input is None:
            parser.error(
                "the following arguments are required: --input/-i (required when not using --build-indexes)"
            )

        convert_vcf(
            args.input,
            args.output,
            args.chunk_size,
            args.max_file_size,
            args.row_group_size,
            args.chromosome,
            args.resume,
            args.overwrite,
        )


if __name__ == "__main__":
    main()
