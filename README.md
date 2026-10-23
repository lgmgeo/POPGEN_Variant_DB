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
   ./tests/run_tests.sh
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
    Example: Count total number of variants in the database
    ```bash
    duckdb db/snvindel.200variants.db -c "SELECT COUNT(*) FROM variants;"
    ```

3. **Structure of the DuckDB Database**

    ```bash
    duckdb db/snvindel.200variants.db -c "DESCRIBE variants;"
    ┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
    │                                                           variants                                                           │
    │                                                                                                                              │
    │ variant_key          varchar    chrom                varchar    pos                  bigint     id                   varchar │
    │ ref                  varchar    alt                  varchar    qual                 double     filter               varchar │
    │ variant_type         varchar    AC                   bigint     AC_Hom               bigint     AC_Hom_XX            bigint  │
    │ AC_Hom_XY            bigint     AC_Hom_raw           bigint     AC_XX                bigint     AC_XY                bigint  │
    │ AC_adj               bigint     AC_grpmax            bigint     AC_raw               bigint     AF                   double  │
    │ AF_XX                double     AF_XY                double     AF_grpmax            double     AF_raw               double  │
    │ AN                   bigint     AN_XX                bigint     AN_XY                bigint     AN_grpmax            bigint  │
    │ AN_raw               bigint     AS_AltDP             bigint     AS_BaseQRankSum      double     AS_FS                double  │
    │ AS_FilterStatus      varchar    AS_MQ                double     AS_MQRankSum         double     AS_QD                double  │
    │ AS_QUALapprox        varchar    AS_ReadPosRankSum    double     AS_SOR               double     AS_VQSLOD            varchar │
    │ AS_VarDP             varchar    AS_culprit           varchar    BaseQRankSum         double     DP                   bigint  │
    │ END                  bigint     ExcessHet            double     ExtGC                boolean    FS                   double  │
    │ LowComp              boolean    LowMapp              boolean    MBQ                  bigint     MFRL                 bigint  │
    │ MMQ                  bigint     MPOS                 bigint     MQ                   double     MQRankSum            double  │
    │ MQ_DP                bigint     QD                   double     QUALapprox           bigint     RAW_MQandDP          bigint  │
    │ ReadPosRankSum       double     SB_TABLE             bigint     SOR                  double     SegDup               boolean │
    │ VarDP                bigint     grpmax               varchar    inGnomad             boolean    notCoveredByGnomad   boolean │
    │ passGnomad           boolean    csq_Consequence      varchar    csq_IMPACT           varchar    csq_SYMBOL           varchar │
    │ csq_Gene             varchar    csq_Feature_type     varchar    csq_Feature          varchar    csq_BIOTYPE          varchar │
    │ csq_EXON             varchar    csq_INTRON           varchar    csq_CDS_position     varchar    csq_Protein_position varchar │
    │ csq_Amino_acids      varchar    csq_Codons           varchar                                                                 │
    └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
    ```

## REST API

The project includes a REST API built with [FastAPI](https://fastapi.tiangolo.com/) to query variant annotations stored in a DuckDB database.

The API provides HTTP endpoints for retrieving variants using genomic coordinates, gene symbols and predicted consequences. Interactive API documentation is available through Swagger UI.

### Installation

Install the API dependencies:

```bash
python -m pip install fastapi "uvicorn[standard]" duckdb
```

Install the other project dependencies as described in the installation section.

### Configuration

By default, the API uses the DuckDB database:

```text
db/snvindel.200variants.db
```

To use another database, set the `POPGEN_DB` environment variable to its path.

For example:

```bash
export POPGEN_DB="db/my_variants.db"
```

The database is opened in read-only mode.

### Starting the API

From the project root directory, run:

```bash
python -m uvicorn api.main:app --reload
```

By default, the API is available at `http://localhost:8000`.

The `--reload` option is intended for development and automatically restarts the server when the source code changes.

### Endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | Checks API and database availability. |
| `GET /variants` | Retrieves variants with optional filters. |
| `GET /docs` | Opens the interactive Swagger UI documentation. |

#### Querying variants

The `GET /variants` endpoint supports the following query parameters:

| Parameter | Description |
|---|---|
| `gene` | Filters by gene symbol (`csq_SYMBOL`). |
| `chrom` | Filters by chromosome. |
| `start` | Minimum genomic position. |
| `end` | Maximum genomic position. |
| `consequence` | Filters by predicted consequence (`csq_Consequence`). |
| `limit` | Maximum number of rows returned (default defined by the API, maximum 500). |
| `offset` | Number of rows to skip, for pagination. |

All filters are optional and can be combined.

Example: retrieve up to 10 annotation rows for the gene `DDX11L1`:

```text
http://localhost:8000/variants?gene=DDX11L1&limit=10
```

Example: filter variants on chromosome 1 between positions 1000 and 2000:

```text
http://localhost:8000/variants?chrom=chr1&start=1000&end=2000
```

Example: paginate through results in batches of 100 rows:

```text
http://localhost:8000/variants?gene=DDX11L1&limit=100&offset=0
```

Change `offset` to `100`, `200`, and so on to retrieve subsequent batches.

**Note:** The API returns annotation rows. A single genomic variant may therefore appear in multiple rows when it has annotations for different transcripts.

### Interactive documentation

Once the API is running, open:

- Swagger UI: `http://localhost:8000/docs`
- OpenAPI specification: `http://localhost:8000/openapi.json`

Swagger UI can be used to inspect the available parameters and test requests directly from a browser.

### Development status

The API currently provides basic read-only querying of the DuckDB database. Authentication, production deployment, and performance testing under concurrent load should be addressed before exposing the service publicly.
