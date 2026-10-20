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
# We assume the script is in 'tests/' or 'scripts/', so we go up one level.
# If the script is in 'tests/', PROJECT_ROOT becomes the parent.
# If the script is in the root, it stays the root.
if [ -d "$SCRIPT_DIR/.." ]; then
    PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
else
    PROJECT_ROOT="$SCRIPT_DIR"
fi

# Now all paths are relative to PROJECT_ROOT
# ----------------------------

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}Project Root: $PROJECT_ROOT${NC}"
echo -e "${BLUE}Starting Integration Test Suite...${NC}\n"

# Configuration of paths relative to PROJECT_ROOT
INPUT_VCF="$PROJECT_ROOT/tests/synthetic_catalogue_snvindel.200variants.vcf.gz"
OUTPUT_DIR="$PROJECT_ROOT/data/processed/snvindel.200variants"
rm -rf "$OUTPUT_DIR"  # Clean up any previous test output
DB_OUTPUT="$PROJECT_ROOT/db/snvindel.200variants.db"
rm -f "$DB_OUTPUT"  # Clean up any previous test database
LOG_DIR="$PROJECT_ROOT/logs"

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
    --output "$OUTPUT_DIR" \
    --chromosome chr1 \
    --overwrite \
    --log-level DEBUG

# Verification
if [ -d "$OUTPUT_DIR/variants/chrom=chr1" ] && [ "$(ls -A $OUTPUT_DIR/variants/chrom=chr1 2>/dev/null || true)" ]; then
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
    --output "$OUTPUT_DIR" \
    --resume \
    --log-level DEBUG

print_status "Resume command executed without error."

# -----------------------------------------------------------------------------
# STEP 3: Build Lookup Indexes
# -----------------------------------------------------------------------------
echo -e "\n${BLUE}Step 3: Building lookup indexes (--build-indexes)...${NC}"
python3 "$PROJECT_ROOT/scripts/variant2parquet__snvindel.py" \
    --output "$OUTPUT_DIR" \
    --build-indexes \
    --log-level DEBUG

# Verification of index files
INDEX_FILES=("id_index.parquet" "gene_index.parquet" "feature_index.parquet")
for file in "${INDEX_FILES[@]}"; do
    if [ -f "$OUTPUT_DIR/$file" ]; then
        echo -e "${GREEN}[PASS]${NC} Index created: $file"
    else
        echo -e "${RED}[FAIL]${NC} Index missing: $file"
        exit 1
    fi
done

# -----------------------------------------------------------------------------
# STEP 4: Build DuckDB Database
# -----------------------------------------------------------------------------
echo -e "\n${BLUE}Step 4: Building DuckDB database...${NC}"
python3 "$PROJECT_ROOT/scripts/build_db.py" \
  --input_dir "$OUTPUT_DIR/variants" \
  --db_output "$DB_OUTPUT" 

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
echo -e "\n${BLUE}Step 5: Verifying VEP annotation count...${NC}"

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
echo -e "Output Directory: $OUTPUT_DIR"
echo -e "Database:         $DB_OUTPUT"
echo -e "Logs:             $LOG_DIR"