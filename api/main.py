import os
from contextlib import asynccontextmanager, closing
from pathlib import Path

import duckdb
from fastapi import FastAPI, HTTPException, Query

# Database path can be overridden with the POPGEN_DB environment variable.
DB_PATH = Path(os.environ.get("POPGEN_DB", "db/snvindel.200variants.db")).resolve()


def get_connection():
    """Open a read-only connection to the DuckDB database."""
    if not DB_PATH.is_file():
        raise RuntimeError(f"Database not found: {DB_PATH}")

    return duckdb.connect(str(DB_PATH), read_only=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate database availability and required columns at startup."""
    try:
        with closing(get_connection()) as con:
            columns = {row[0] for row in con.execute("DESCRIBE variants").fetchall()}

            required = {
                "chrom",
                "pos",
                "ref",
                "alt",
                "id",
                "AF",
                "AC",
                "AN",
                "filter",
                "csq_SYMBOL",
                "csq_Consequence",
                "csq_Feature",
                "notCoveredByGnomad",
                "inGnomad",
                "passGnomad",
            }

            missing = required - columns
            if missing:
                raise RuntimeError(
                    f"Missing columns in variants table: {sorted(missing)}"
                )

            con.execute("SELECT 1").fetchone()

    except Exception as exc:
        raise RuntimeError(
            f"Cannot initialize API using database {DB_PATH}: {exc}"
        ) from exc

    yield


app = FastAPI(
    title="POPGEN Variant API",
    description="Read-only API for querying annotated genetic variants.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    """Check API and database availability."""
    try:
        with closing(get_connection()) as con:
            con.execute("SELECT 1").fetchone()

        return {"status": "ok", "database": "ok"}

    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )


@app.get("/variants")
def search_variants(
    # Variant identification and coordinates
    id: str | None = Query(default=None, min_length=1, max_length=200),
    chrom: str | None = Query(default=None, min_length=1, max_length=30),
    pos: int | None = Query(default=None, ge=1),
    start: int | None = Query(default=None, ge=1),
    end: int | None = Query(default=None, ge=1),
    # Transcript annotations
    gene: str | None = Query(default=None, min_length=1, max_length=100),
    feature: str | None = Query(default=None, min_length=1, max_length=100),
    consequence: list[str] | None = Query(default=None),
    # FILTER values; multiple values are allowed
    filter: list[str] | None = Query(default=None),
    # gnomAD flags
    not_covered_by_gnomad: bool | None = Query(default=None),
    in_gnomad: bool | None = Query(default=None),
    pass_gnomad: bool | None = Query(default=None),
    # Allele count, allele number and allele frequency
    ac_min: int | None = Query(default=None, ge=0),
    ac_max: int | None = Query(default=None, ge=0),
    an_min: int | None = Query(default=None, ge=0),
    an_max: int | None = Query(default=None, ge=0),
    af_min: float | None = Query(default=None, ge=0, le=1),
    af_max: float | None = Query(default=None, ge=0, le=1),
    # Pagination
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Search unique variants using optional coordinate and annotation filters."""

    if start is not None and end is not None and start > end:
        raise HTTPException(
            status_code=422,
            detail="'start' must be less than or equal to 'end'",
        )

    for minimum, maximum, name in (
        (ac_min, ac_max, "AC"),
        (an_min, an_max, "AN"),
        (af_min, af_max, "AF"),
    ):
        if minimum is not None and maximum is not None and minimum > maximum:
            raise HTTPException(
                status_code=422,
                detail=f"The minimum {name} must be less than or equal to its maximum",
            )

    conditions = []
    params = []

    # Variant identification and coordinates
    if id is not None:
        conditions.append("id = ?")
        params.append(id)

    if chrom is not None:
        conditions.append("chrom = ?")
        params.append(chrom)

    if pos is not None:
        conditions.append("pos = ?")
        params.append(pos)

    if start is not None:
        conditions.append("pos >= ?")
        params.append(start)

    if end is not None:
        conditions.append("pos <= ?")
        params.append(end)

    # Transcript annotations
    if gene is not None:
        conditions.append("csq_SYMBOL = ?")
        params.append(gene)

    if feature is not None:
        conditions.append("csq_Feature = ?")
        params.append(feature)

    if consequence:
        if any(not value.strip() for value in consequence):
            raise HTTPException(
                status_code=422,
                detail="Consequence values must not be empty",
            )

        conditions.append("list_has_any(string_split(csq_Consequence, '&'), ?)")
        params.append(consequence)

    # FILTER can contain semicolon-separated values.
    if filter:
        if any(not value.strip() for value in filter):
            raise HTTPException(
                status_code=422,
                detail="FILTER values must not be empty",
            )

        conditions.append("list_has_any(string_split(\"filter\", ';'), ?)")
        params.append(filter)

    # gnomAD flags. Explicit False values must also be applied.
    if not_covered_by_gnomad is not None:
        conditions.append('"notCoveredByGnomad" = ?')
        params.append(not_covered_by_gnomad)

    if in_gnomad is not None:
        conditions.append('"inGnomad" = ?')
        params.append(in_gnomad)

    if pass_gnomad is not None:
        conditions.append('"passGnomad" = ?')
        params.append(pass_gnomad)

    # AC filters
    if ac_min is not None:
        conditions.append('"AC" >= ?')
        params.append(ac_min)

    if ac_max is not None:
        conditions.append('"AC" <= ?')
        params.append(ac_max)

    # AN filters
    if an_min is not None:
        conditions.append('"AN" >= ?')
        params.append(an_min)

    if an_max is not None:
        conditions.append('"AN" <= ?')
        params.append(an_max)

    # AF filters
    if af_min is not None:
        conditions.append('"AF" >= ?')
        params.append(af_min)

    if af_max is not None:
        conditions.append('"AF" <= ?')
        params.append(af_max)

    # DISTINCT removes duplicate transcript rows because the selected
    # variant-level fields are identical for all transcripts of a variant.
    sql = """
        SELECT DISTINCT
            chrom,
            pos,
            ref,
            alt,
            id,
            "AF" AS af,
            "AC" AS ac,
            "AN" AS an
        FROM variants
    """

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    # Pagination is applied after DISTINCT, so it operates on unique variants.
    sql += """
        ORDER BY chrom, pos, ref, alt, id
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    try:
        with closing(get_connection()) as con:
            result = con.execute(sql, params)
            names = [item[0] for item in result.description]
            rows = result.fetchall()

        return {
            "count": len(rows),
            "limit": limit,
            "offset": offset,
            "results": [dict(zip(names, row)) for row in rows],
        }

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Database query failed",
        )
