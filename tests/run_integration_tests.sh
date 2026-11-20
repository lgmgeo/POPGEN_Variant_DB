#!/bin/bash

# =============================================================================
# Test Suite for variant2parquet__snvindel
# =============================================================================

# Exit immediately if a command exits with a non-zero status
set -e

# --- SMART PATH DETECTION ---
# Get the absolute path of the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Find the project root (the directory containing 'scripts', 'data', etc.) 
# The script may be located in the project root, tests/, or scripts/.
if [ -d "$SCRIPT_DIR/scripts" ]; then
    PROJECT_ROOT="$SCRIPT_DIR"
elif [ -d "$SCRIPT_DIR/../scripts" ]; then
    PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
else
    echo "Cannot locate the project root (scripts/ directory not found)." >&2
    exit 1
fi

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color


# --- DATASET PATH CONFIGURATION ---
DATASET_NAME="snvindel.200variants"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dataset)
            if [[ -z "${2:-}" || "$2" == -* ]]; then
                echo "Error: --dataset requires a dataset name." >&2
                exit 1
            fi
            DATASET_NAME="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [--dataset DATASET_NAME]"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

DATASET_DIR="$PROJECT_ROOT/data/datasets/$DATASET_NAME"


# --- LOGGING ---
LOG_DIR="$DATASET_DIR/logs"
mkdir -p "$LOG_DIR"

SCRIPT_NAME=$(basename "$0" .sh)
LOG_FILE="$LOG_DIR/${SCRIPT_NAME}_$(date +%Y%m%d).log"

# Display output in the terminal and save a color-free copy to the log file.
exec > >(tee >(sed -u $'s/\033\\[[0-9;]*m//g' > "$LOG_FILE")) 2>&1


# All paths below are absolute paths relative to PROJECT_ROOT.
# ----------------------------

# Configuration of paths relative to PROJECT_ROOT
INPUT_VCF="$DATASET_DIR/raw/synthetic_catalogue_snvindel.200variants.vcf.gz"
OUTPUT_DIR="$DATASET_DIR/parquet"
LOOKUP_DIR="$DATASET_DIR/lookup_files"
DB_OUTPUT="$DATASET_DIR/duckdb/snvindel.200variants.db"

echo -e "${BLUE}Project Root: $PROJECT_ROOT${NC}"
echo -e "${BLUE}Input file: $INPUT_VCF${NC}"
echo -e "${BLUE}Starting Integration Test Suite...${NC}\n"

# Clean up previous test outputs to ensure a fresh integration test.
rm -rf "$OUTPUT_DIR"
rm -rf "$LOOKUP_DIR"
rm -f "$DB_OUTPUT"

# Create output parent directories required by the test.
mkdir -p "$(dirname "$OUTPUT_DIR")"
mkdir -p "$(dirname "$LOOKUP_DIR")"
mkdir -p "$(dirname "$DB_OUTPUT")"
mkdir -p "$LOG_DIR"

# Function to print status
print_status() {
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}[PASS]${NC} $1"
    else
        echo -e "${RED}[FAIL]${NC} $1"
        exit 1
    fi
}

# -----------------------------------------------------------------------------
# STEP 1: Fresh Conversion (Chromosome 1 only)
# -----------------------------------------------------------------------------
echo -e "${BLUE}Step 1: Running fresh conversion for chr1 (--chromosome chr1 --overwrite)...${NC}"
python3 "$PROJECT_ROOT/scripts/variant2parquet__snvindel.py" \
    --input "$INPUT_VCF" \
    --dataset "$DATASET_DIR" \
    --lookup-output "$LOOKUP_DIR" \
    --chromosome chr1 \
    --overwrite \
    --log-level DEBUG

# Verification
if [ -d "$OUTPUT_DIR/variants/chrom=chr1" ] && [ "$(ls -A "$OUTPUT_DIR/variants/chrom=chr1" 2>/dev/null || true)" ]; then
    print_status "Conversion of chr1 successful and files created."
else
    echo -e "${RED}[FAIL]${NC} Conversion of chr1 failed (files missing or empty)."
    exit 1
fi

# -----------------------------------------------------------------------------
# STEP 2: Resume Capability
# -----------------------------------------------------------------------------
echo -e "\n${BLUE}Step 2: Testing Resume capability (--resume)...${NC}"
python3 "$PROJECT_ROOT/scripts/variant2parquet__snvindel.py" \
    --input "$INPUT_VCF" \
    --dataset "$DATASET_DIR" \
    --lookup-output "$LOOKUP_DIR" \
    --resume \
    --log-level DEBUG

print_status "Resume command executed without error."

# -----------------------------------------------------------------------------
# STEP 3: Build Lookup Files
# -----------------------------------------------------------------------------
echo -e "\n${BLUE}Step 3: Building lookup files (--build-lookup)...${NC}"
python3 "$PROJECT_ROOT/scripts/variant2parquet__snvindel.py" \
    --dataset "$DATASET_DIR" \
    --lookup-output "$LOOKUP_DIR" \
    --build-lookup \
    --log-level DEBUG

# Verify that all lookup files are created in the dedicated lookup directory.
LOOKUP_FILES=("id_chrom.parquet" "feature_chrom_symbol.parquet" "impact.txt" "consequences.txt" "chromosomes.txt")
for file in "${LOOKUP_FILES[@]}"; do
    if [ -f "$LOOKUP_DIR/$file" ]; then
        echo -e "${GREEN}[PASS]${NC} Lookup file created: $LOOKUP_DIR/$file"
    else
        echo -e "${RED}[FAIL]${NC} Lookup file missing: $LOOKUP_DIR/$file"
        exit 1
    fi
done

# -----------------------------------------------------------------------------
# STEP 4: Build DuckDB Database
# -----------------------------------------------------------------------------
echo -e "\n${BLUE}Step 4: Building DuckDB database...${NC}"
python3 "$PROJECT_ROOT/scripts/build_duckdb.py" \
    --dataset "$DATASET_DIR"
 

# Verification of DB file
if [ -f "$DB_OUTPUT" ]; then
    print_status "DuckDB database created: $DB_OUTPUT"
else
    echo -e "${RED}[FAIL]${NC} DuckDB database creation failed."
    exit 1
fi


# -----------------------------------------------------------------------------
# STEP 5: Verify Total Variant Count
# -----------------------------------------------------------------------------
echo -e "\n${BLUE}Step 5: Verifying total variant count...${NC}"

# Count variants in the source VCF/BCF using bcftools
SOURCE_COUNT=$(bcftools index -n "$INPUT_VCF")

# Count distinct variant IDs in the DuckDB database
DB_COUNT=$(python3 - "$DB_OUTPUT" <<'PY'
import duckdb
import sys

db_path = sys.argv[1]

with duckdb.connect(db_path, read_only=True) as con:
    count = con.execute(
        "SELECT COUNT(DISTINCT ID) FROM variants"
    ).fetchone()[0]

print(count)
PY
)

echo "Variants in BCF/VCF source file: $SOURCE_COUNT"
echo "Distinct variant IDs in duckdb database: $DB_COUNT"

if [ "$SOURCE_COUNT" -eq "$DB_COUNT" ]; then
    echo -e "${GREEN}[PASS]${NC} Variant count matches: $SOURCE_COUNT"
else
    echo -e "${RED}[FAIL]${NC} Variant count mismatch!"
    exit 1
fi


# -----------------------------------------------------------------------------
# STEP 6: Verify VEP Annotation Count
# -----------------------------------------------------------------------------
echo -e "\n${BLUE}Step 6: Verifying VEP annotation count...${NC}"

# Count VEP annotations in the source VCF
SOURCE_COUNT=$(bcftools query -f '%INFO/CSQ\n' "$INPUT_VCF" |
    awk -F',' '{ total += NF } END { print total }')

# Count rows in the DuckDB variants table
DB_COUNT=$(python3 - "$DB_OUTPUT" <<'PY'
import duckdb
import sys

with duckdb.connect(sys.argv[1], read_only=True) as con:
    count = con.execute("SELECT COUNT(*) FROM variants").fetchone()[0]

print(count)
PY
)

echo "VEP annotations in source: $SOURCE_COUNT"
echo "Rows in DuckDB:            $DB_COUNT"

if [ "$SOURCE_COUNT" -eq "$DB_COUNT" ]; then
    echo -e "${GREEN}[PASS]${NC} VEP annotation count matches: $SOURCE_COUNT"
else
    echo -e "${RED}[FAIL]${NC} VEP annotation count mismatch!"
    exit 1
fi

# -----------------------------------------------------------------------------
# FINAL SUMMARY
# -----------------------------------------------------------------------------
echo -e "\n${BLUE}========================================${NC}"
echo -e "${GREEN}  ALL TESTS PASSED SUCCESSFULLY!       ${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "Parquet Directory: $OUTPUT_DIR"
echo -e "Lookup Directory:  $LOOKUP_DIR"
echo -e "Database:          $DB_OUTPUT"
echo -e "Logs:              $LOG_DIR"
