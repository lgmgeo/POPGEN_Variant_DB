# POPGEN_Variant_DB

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: GPLv3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

**POPGEN_Variant_DB** is a bioinformatics pipeline designed to convert large-scale genomic variant files (VCF/BCF) into highly optimized, denormalized Parquet datasets and DuckDB databases.

The pipeline is specifically engineered to flatten complex VEP (Variant Effect Predictor) annotations, allowing for seamless, high-speed analytical queries across millions of variants and transcripts.

## Key Features

### Variant to Parquet Conversion (`variant2parquet__snvindel.py`)
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
   git clone git@github.com:lgmgeo/POPGEN_Variant_DB.git
   cd POPGEN_Variant_DB
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
   
## Usage: Variant to Parquet  
```bash
    usage: variant2parquet__snvindel.py [-h] [--input FILE] --output DIR [--chromosome CHROM] [--resume] [--overwrite] [--build-indexes] [--chunk-size N]
                                        [--max-file-size BYTES] [--row-group-size N] [--log-level LEVEL]

    options:
    -h, --help            show this help message and exit

    input and output:
    --input FILE, -i FILE
                            Input annotated VCF/BCF file (required for conversion).
    --output DIR, -o DIR  Output directory for the Parquet dataset, indexes, and summaries.

    processing options:
    --chromosome CHROM, -c CHROM
                            Process only the specified chromosome, reprocessing it from scratch.
    --resume              Resume conversion using checkpoint.json and skip completed chromosomes.
    --overwrite           Overwrite existing output. Replaces the entire output directory, unless --chromosome is specified.
    --build-indexes       Build lookup indexes and text summaries from existing Parquet files.

    performance options:
    --chunk-size N        Number of rows buffered per chromosome before writing a batch.
    --max-file-size BYTES
                            Approximate maximum Parquet file size (in bytes) before rotating to a new file.
    --row-group-size N    Maximum number of rows per Parquet row group.

    logging options:
    --log-level LEVEL     Set the logging verbosity.
    
```

1. **Convert VCF/BCF to Partitioned Parquet**

   To start a new conversion (or overwrite existing data):
   ```bash
    python3 ./scripts/variant2parquet__snvindel.py --input tests/synthetic_catalogue_snvindel.200variants.vcf.gz --output data/processed/snvindel.200variants
   ```

   To resume an interrupted conversion using a checkpoint:
   ```bash
    python3 ./scripts/variant2parquet__snvindel.py --input tests/synthetic_catalogue_snvindel.200variants.vcf.gz --output data/processed/snvindel.200variants --resume
    ```

    To focuse on a specific chromosome:
    ```bash
    python3 ./scripts/variant2parquet__snvindel.py --input tests/synthetic_catalogue_snvindel.200variants.vcf.gz --output data/processed/snvindel.200variants --chromosome chr1
    ```

    To restart the conversion from scratch:
    ```bash
    python3 ./scripts/variant2parquet__snvindel.py --input tests/synthetic_catalogue_snvindel.200variants.vcf.gz --output data/processed/snvindel.200variants --overwrite
     ```

2. **Build lookup indexes and text summaries for the user interface**

    - **Indexes:** feature_index.parquet, gene_index.parquet and id_index.parquet
    - **Text summaries:** Impact.txt, consequences.txt, features.tsv and genes.tsv  

    ```bash
    python3 ./scripts/variant2parquet__snvindel.py --output data/processed/snvindel.200variants --build-indexes
    ```

2. **Output structure**

    ```bash
    data/processed/snvindel.200variants/
    ├── Impact.txt
    ├── checkpoint.json
    ├── consequences.txt
    ├── feature_index.parquet
    ├── features.tsv
    ├── gene_index.parquet
    ├── genes.tsv
    ├── id_index.parquet
    └── variants
        ├── chrom=chr1
        │   └── part-000000.parquet
        │── chrom=chr2
        │   └── part-000000.parquet
        └── ...
    ```

## Usage: DuckDB Indexing

```bash
usage: build_db.py [-h] --input_dir DIR --db_output FILE

Build an indexed DuckDB database from a partitioned Parquet variant dataset.

options:
-h, --help            show this help message and exit

required arguments:
--input_dir DIR, -i DIR
                        Directory containing the partitioned Parquet dataset, organized into chromosome-specific subdirectories (e.g. chrom=chr1,
                        chrom=chr2, ...).
--db_output FILE, -o FILE
                        Path to the output DuckDB database file.
```

1. **Build the Optimized DuckDB Database**

    ```bash
    python ./scripts/build_db.py --input_dir data/processed/snvindel.200variants/variants --db_output db/snvindel.200variants.db
    ```

2. **Querying the DuckDB Database**
    
    Example: Find all variants in a specific gene
    ```bash
    duckdb db/snvindel.200variants.db -c "SELECT chrom, pos, ref, alt, csq_Consequence FROM variants WHERE csq_SYMBOL = 'DDX11L1'; "
    ```
