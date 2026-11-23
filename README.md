# POPGEN_Variant_DB

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: GPLv3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

**POPGEN_Variant_DB** is a bioinformatics pipeline designed to convert large-scale genomic variant files (VCF/BCF) into highly optimized, denormalized Parquet datasets and DuckDB databases.


## Key Features

### Variant to Parquet Conversion (`variant2parquet__snvindel.py`)
- **Flattened Schema**: Each row represents exactly one transcript-variant pair, integrating all INFO and CSQ fields into a single table.
- **Streaming Architecture**: Processes records one-by-one to handle massive datasets (30GB+) with minimal RAM footprint.
- **Hive-style Partitioning**: Automatic partitioning by chromosome (`chrom=.../`) for efficient data skipping.
- **Robust Typing**: Strict mapping of VCF INFO fields (Flag, Integer, Float, String) to PyArrow types.
- **Resumability**: Integrated checkpointing system to resume interrupted processing.
- **Lookup**: Build lookup Parquet and text files used by the web interface.

### DuckDB Database Creation (`build_duckdb.py`)
- **High-Speed Analytics**: Converts Parquet partitions into a single, ultra-fast DuckDB database.

### DuckDB Indexing (`build_indexes.py`)
- **Automated Indexing**: Builds specialized indices for:
  - **Transcripts** (`csq_Feature`)
  - **Variant IDs** (`id`)
  - **Genes** (`SYMBOL`)

### Dataset directory structure
```text
data/
└── datasets/
    └── snvindel.200variants/
        ├── raw/
        │   ├──synthetic_catalogue_snvindel.200variants.vcf.gz
        │   └── synthetic_catalogue_snvindel.200variants.vcf.gz.csi
        ├── tmp/
        ├── parquet/
        │   ├── checkpoint.json
        │   └── variants/
        │       ├── chrom=chr1/
        │       │   └── part-000000.parquet
        │       ├── chrom=chr2/
        │       │   └── part-000000.parquet
        │       └── ...
        ├── lookup_files/
        │   ├── feature_chrom_symbol.parquet
        │   ├── id_chrom.parquet
        │   ├── impact.txt
        │   ├── consequences.txt
        │   └── chromosomes.txt
        ├── duckdb/
        │   └── snvindel.200variants.db
        └── logs/
```

## Installation

1. **Clone the repository**
   ```bash
   git clone git@github.com:lgmgeo/POPGEN_Variant_DB.git
   cd POPGEN_Variant_DB
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Tests with 200 variants**
   ```bash
   ./tests/run_integration_tests.sh
   ```

## Usage: Variant to Parquet

The `variant2parquet__snvindel.py` script converts annotated VCF/BCF files into chromosome-partitioned Parquet datasets.

### Command-line options

```bash
usage: variant2parquet__snvindel.py [-h] --dataset DIR [--input FILE]
                                    [--chromosome CHROM] [--resume]
                                    [--overwrite] [--build-lookup]
                                    [--chunk-size N] [--max-file-size BYTES]
                                    [--row-group-size N] [--log-level LEVEL]
```

- `--dataset DIR`: Dataset directory under `data/datasets/` (required).
- `--input FILE`: Input annotated VCF/BCF file (required for conversion, not required with `--build-lookup`).
- `--chromosome CHROM`: Process only the specified chromosome.
- `--resume`: Resume an interrupted conversion using `checkpoint.json`.
- `--overwrite`: Overwrite existing conversion output.
- `--build-lookup`: Generate lookup Parquet and text files used by the web interface.
- `--chunk-size N`: Number of rows buffered per chromosome before writing a batch.
- `--max-file-size BYTES`: Approximate maximum Parquet file size before rotating to a new file.
- `--row-group-size N`: Maximum number of rows per Parquet row group.
- `--log-level LEVEL`: Set the logging verbosity.

### Convert VCF/BCF to partitioned Parquet

To start a new conversion:

```bash
python3 ./scripts/variant2parquet__snvindel.py \
    --dataset data/datasets/snvindel.200variants \
    --input tests/synthetic_catalogue_snvindel.200variants.vcf.gz
```

To resume an interrupted conversion:

```bash
python3 ./scripts/variant2parquet__snvindel.py \
    --dataset data/datasets/snvindel.200variants \
    --input tests/synthetic_catalogue_snvindel.200variants.vcf.gz \
    --resume
```

To process a specific chromosome:

```bash
python3 ./scripts/variant2parquet__snvindel.py \
    --dataset data/datasets/snvindel.200variants \
    --input tests/synthetic_catalogue_snvindel.200variants.vcf.gz \
    --chromosome chr1
```

To restart the conversion from scratch:

```bash
python3 ./scripts/variant2parquet__snvindel.py \
    --dataset data/datasets/snvindel.200variants \
    --input tests/synthetic_catalogue_snvindel.200variants.vcf.gz \
    --overwrite
```

### Build lookup Parquet and text files

After conversion, generate the lookup files used by the web interface:

```bash
python3 ./scripts/variant2parquet__snvindel.py \
    --dataset data/datasets/snvindel.200variants \
    --build-lookup
```

Generated files include:

- **Parquet:** `feature_chrom_symbol.parquet`, `id_chrom.parquet`
- **Text summaries:** `impact.txt`, `consequences.txt`, `chromosomes.txt`

## DuckDB Database Creation (`build_duckdb.py`)

The `build_duckdb.py` script creates a DuckDB database from an existing partitioned Parquet dataset.

Database creation is performed separately from VCF/BCF conversion.

### Usage

```bash
usage: build_duckdb.py [-h] --dataset DIR [--log-level LEVEL]
```

Options:

- `--dataset DIR`: Dataset directory containing the `parquet/` directory (required).
- `--log-level LEVEL`: Set the logging verbosity.

### Build the DuckDB database

Run the script after completing the Parquet conversion:

```bash
python3 ./scripts/build_duckdb.py \
    --dataset data/datasets/snvindel.200variants
```

The database is created automatically in:

```text
data/datasets/snvindel.200variants/duckdb/snvindel.200variants.db
```

The script refuses to overwrite an existing database.

### Inspect the database structure

```bash
duckdb data/datasets/snvindel.200variants/duckdb/snvindel.200variants.db \
    -c "DESCRIBE variants;"
```


## DuckDB Indexing (`build_indexes.py`)

The `build_indexes.py` script creates indexes on an existing DuckDB database to accelerate queries on variant identifiers, transcripts and gene symbols.

Index creation is performed separately from database construction to allow memory usage to be controlled and indexing to be resumed if interrupted.

### Indexes

The script creates the following indexes on the `variants` table:

| Index | Column | Purpose |
|---|---|---|
| `idx_feature` | `csq_Feature` | Transcript-based queries |
| `idx_id` | `id` | Variant ID lookups |
| `idx_symbol` | `csq_SYMBOL` | Gene symbol queries |

### Usage

```bash
usage: build_indexes.py [-h] --dataset DIR
                        [--memory-limit MEMORY_LIMIT]
                        [--threads THREADS]
                        [--log-level LEVEL]
```

Run the script after creating the database with `build_duckdb.py`:

```bash
python3 ./scripts/build_indexes.py \
    --dataset data/datasets/snvindel.200variants
```

### Memory and performance settings

Index creation can require substantial memory, particularly for large variant datasets.

The script supports options to limit memory usage and configure the number of threads.

For example:

```bash
python3 ./scripts/build_indexes.py \
    --dataset data/datasets/snvindel.200variants \
    --memory-limit 32GB \
    --threads 1
```

Adjust these values according to the available system resources and the size of the database.

### Resuming interrupted indexing

The script checks which indexes already exist and skips them when indexing is restarted.

Successfully created indexes are checkpointed, allowing the process to resume without rebuilding completed indexes.

## Querying the DuckDB Database

Once the database has been created, you can query it directly using the DuckDB command-line interface.

**Example: Retrieve variants associated with a gene**

```bash
duckdb data/datasets/snvindel.200variants/duckdb/snvindel.200variants.db \
    -c "SELECT chrom, pos, ref, alt, csq_Consequence FROM variants WHERE csq_SYMBOL = 'DDX11L1';"
```

**Example: Count the total number of rows**

```bash
duckdb data/datasets/snvindel.200variants/duckdb/snvindel.200variants.db \
    -c "SELECT COUNT(*) FROM variants;"
```

**Example: Retrieve variants from a specific chromosome and genomic region**

```bash
duckdb data/datasets/snvindel.200variants/duckdb/snvindel.200variants.db \
    -c "SELECT chrom, pos, ref, alt, id FROM variants WHERE chrom = 'chr1' AND pos BETWEEN 10000 AND 20000;"
```

These commands allow direct inspection of the database without going through the REST API.

## REST API

The project provides a REST API built with FastAPI to query the DuckDB variant database.

### Starting the API

From the project root directory:

```bash
python -m uvicorn api.main:app --reload
```

By default, the API uses the following database:

```text
data/datasets/snvindel.200variants/duckdb/snvindel.200variants.db
```

To use another database, set the `POPGEN_DB` environment variable before starting the API.

For example:

```bash
export POPGEN_DB=data/datasets/my_dataset/duckdb/my_dataset.db
python -m uvicorn api.main:app --reload
```

The API is then available at:

```text
http://127.0.0.1:8000
```

Interactive API documentation is available at:

```text
http://127.0.0.1:8000/docs
```

### Available endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | Checks whether the API is running and the database is accessible. |
| `GET /database` | Returns the name of the database used by the API. |
| `GET /variants` | Retrieves variants matching the specified filters. |

### Querying variants

The `/variants` endpoint supports the following query parameters:

| Parameter | Description |
|---|---|
| `id` | Variant identifier. |
| `chrom` | Chromosome. |
| `pos` | Exact genomic position. |
| `start` | Start position of a genomic region. |
| `end` | End position of a genomic region. |
| `gene` | Gene symbol (`csq_SYMBOL`). |
| `feature` | Transcript or feature identifier (`csq_Feature`). |
| `consequence` | Variant consequence (`csq_Consequence`). |
| `filter` | Variant filter status. |
| `not_covered_by_gnomad` | Select variants not covered by gnomAD. |
| `in_gnomad` | Select variants present in gnomAD. |
| `pass_gnomad` | Select variants passing the gnomAD criteria. |
| `ac_min`, `ac_max` | Minimum and maximum allele count (AC). |
| `an_min`, `an_max` | Minimum and maximum allele number (AN). |
| `af_min`, `af_max` | Minimum and maximum allele frequency (AF). |
| `limit` | Maximum number of variants returned (maximum: 500). |
| `offset` | Number of variants to skip (default: 0). |

Filters can be combined to narrow down the results.

### Examples

**Retrieve variants associated with a gene:**

```text
http://127.0.0.1:8000/variants?gene=DDX11L1
```

**Retrieve variants from a genomic region:**

```text
http://127.0.0.1:8000/variants?chrom=chr1&start=10000&end=20000
```

**Retrieve variants with a minimum allele frequency:**

```text
http://127.0.0.1:8000/variants?af_min=0.01
```

**Combine several filters and limit the results:**

```text
http://127.0.0.1:8000/variants?gene=DDX11L1&chrom=chr1&limit=50&offset=0
```

### Results

The API returns distinct variants rather than individual transcript annotation rows. Each variant is identified by its chromosome, position, reference allele, alternate allele and variant identifier.

The response includes the variant information and the number of variants returned in the current response. This count refers to the returned results, not to the total number of variants matching the filters.

The `limit` parameter is optional. When specified, it cannot exceed 500 variants per response. The `offset` parameter can be used to retrieve subsequent results.