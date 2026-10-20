# POPGEN_Variant_DB

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: GPLv3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

**POPGEN_Variant_DB** is a high-performance bioinformatics pipeline designed to convert large-scale genomic variant files (VCF/BCF) into highly optimized, denormalized Parquet datasets and DuckDB databases.

The pipeline is specifically engineered to flatten complex VEP (Variant Effect Predictor) annotations, allowing for seamless, high-speed analytical queries across millions of variants and transcripts.

## Key Features

### Variant to Parquet Conversion (`variant2parquet.py`)
- **Flattened Schema**: Each row represents exactly one transcript-variant pair, integrating all INFO and CSQ fields into a single table.
- **Streaming Architecture**: Processes records one-by-one to handle massive datasets (30GB+) with minimal RAM footprint.
- **Hive-style Partitioning**: Automatic partitioning by chromosome (`chrom=.../`) for efficient data skipping.
- **Robust Typing**: Strict mapping of VCF INFO fields (Flag, Integer, Float, String) to PyArrow types.
- **Resumability**: Integrated checkpointing system to resume interrupted processing.

### DuckDB Indexing (`build_db.py`)
- **High-Speed Analytics**: Converts Parquet partitions into a single, ultra-fast DuckDB database.
- **Automated Indexing**: Builds specialized indices for:
  - **Coordinates** (`chrom`, `pos`)
  - **Transcripts** (`Feature`)
  - **Variant IDs** (`id`)
  - **Genes** (`SYMBOL`, `AF`)

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/POPGEN_Variant_DB.git
   cd POPGEN_Variant_DB
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
## Usage

1. **Convert VCF/BCF to Partitioned Parquet**
   To start a new conversion (or overwrite existing data):
   ```bash
    python scripts/variant2parquet__snvindel.py \
    --input data/raw/input_variants.vcf.gz \
    --output data/processed/snvindel \
    --overwrite;
    ```

   To resume an interrupted conversion using a checkpoint:
   ```bash
    python scripts/variant2parquet__snvindel.py \
    --input data/raw/input_variants.vcf.gz \
    --output data/processed/snvindel \
    --resume \
    --log-level DEBUG; 
    ```

    To focuse on a specific chromosome
    ```bash
    python scripts/variant2parquet__snvindel.py \
    --input data/raw/input_variants.vcf.gz \
    --output data/processed/snvindel \
    --chromosome chr1 \
    --overwrite \
    --log-level DEBUG; 
    ```

2. ** Create useful indexes/files for the user interface**
    >> feature_index.parquet  gene_index.parquet  id_index.parquet
    >> Impact.txt  consequences.txt  features.tsv  genes.tsv  
   ```bash
    python scripts/variant2parquet__snvindel.py \
    --output data/processed/snvindel \
    --build-indexes \
    --log-level DEBUG; 
    ```

2. **Build the Optimized DuckDB Database**
    ```bash
    python scripts/build_db.py \
    --input_dir data/processed/snvindel \
    --db_output db/snvindel.db
    ```

3. **Querying the Data**
    -- Example: Find all variants in a specific gene
    ```bash
    SELECT chrom, pos, ref, alt, Consequence 
    FROM 'db/snvindel.db'.variants 
    WHERE SYMBOL = 'BRCA1';
    ```
