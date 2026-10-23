from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

import api.main as api

# Base de test réelle produite par le pipeline.
DB_PATH = Path(__file__).resolve().parents[1] / "db/snvindel.200variants.db"


@pytest.fixture
def client(monkeypatch):
    """Use the real synthetic database in read-only mode."""
    if not DB_PATH.is_file():
        pytest.fail(
            f"Test database not found: {DB_PATH}. "
            "Build the 200-variant test database first."
        )

    monkeypatch.setattr(api, "DB_PATH", DB_PATH)

    with TestClient(api.app) as test_client:
        yield test_client


def get_db_value(query, params=()):
    """Read a value from the test database without modifying it."""
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        row = con.execute(query, params).fetchone()

    return row[0] if row else None


def test_health(client):
    """The API and database should be available."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
    }


def test_variants_return_expected_columns(client):
    """The API should return the eight requested variant-level fields."""
    response = client.get("/variants", params={"limit": 10})

    assert response.status_code == 200

    data = response.json()
    assert data["count"] == len(data["results"])
    assert data["count"] <= 10
    assert data["count"] > 0

    expected_columns = {
        "chrom",
        "pos",
        "ref",
        "alt",
        "id",
        "af",
        "ac",
        "an",
    }

    for variant in data["results"]:
        assert set(variant.keys()) == expected_columns


def test_variants_are_deduplicated(client):
    """Each (chrom, id, pos, ref, alt) variant appears only once."""
    response = client.get("/variants", params={"limit": 500})

    assert response.status_code == 200

    results = response.json()["results"]
    keys = [(v["chrom"], v["id"], v["pos"], v["ref"], v["alt"]) for v in results]

    assert len(keys) == len(set(keys))
    assert len(keys) == 200


def test_filter_by_coordinates(client):
    """Coordinate filters should return variants in the requested interval."""
    sample = get_db_value("SELECT chrom FROM variants LIMIT 1")
    position = get_db_value(
        "SELECT pos FROM variants WHERE chrom = ? LIMIT 1",
        (sample,),
    )

    response = client.get(
        "/variants",
        params={
            "chrom": sample,
            "start": position,
            "end": position,
        },
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert results

    for variant in results:
        assert variant["chrom"] == sample
        assert variant["pos"] == position


def test_filter_by_id(client):
    """The ID filter should find an existing variant."""
    variant_id = get_db_value("""
        SELECT id
        FROM variants
        WHERE id IS NOT NULL AND id != '' AND id != '.'
        LIMIT 1
    """)

    if variant_id is None:
        pytest.skip("No usable variant ID in the test database.")

    response = client.get("/variants", params={"id": variant_id})

    assert response.status_code == 200
    results = response.json()["results"]
    assert results
    assert all(v["id"] == variant_id for v in results)


@pytest.mark.parametrize(
    ("column", "parameter"),
    [
        ("csq_SYMBOL", "gene"),
        ("csq_Feature", "feature"),
        ("csq_Consequence", "consequence"),
        ('"filter"', "filter"),
    ],
)
def test_annotation_filters(client, column, parameter):
    """Annotation filters should work with values present in the database."""
    value = get_db_value(
        f"""
        SELECT {column}
        FROM variants
        WHERE {column} IS NOT NULL AND {column} != ''
        LIMIT 1
        """
    )

    if value is None:
        pytest.skip(f"No usable value found for {column}.")

    # Consequences can contain several terms separated by '&'.
    if parameter == "consequence":
        value = value.split("&")[0]

    response = client.get(
        "/variants",
        params={parameter: value},
    )

    assert response.status_code == 200
    assert response.json()["count"] == len(response.json()["results"])


@pytest.mark.parametrize(
    "parameter",
    [
        "notCoveredByGnomad",
        "inGnomad",
        "passGnomad",
    ],
)
def test_gnomad_boolean_filters(client, parameter):
    """Boolean gnomAD filters should accept true and false."""
    for value in ("true", "false"):
        response = client.get(
            "/variants",
            params={parameter: value},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == len(data["results"])


def test_filter_by_ac_an_af(client):
    """Numeric filters should accept valid ranges."""
    response = client.get(
        "/variants",
        params={
            "ac_min": 0,
            "an_min": 0,
            "af_min": 0,
            "af_max": 1,
        },
    )

    assert response.status_code == 200
    assert response.json()["count"] == len(response.json()["results"])


def test_pagination(client):
    """Consecutive pages should not contain duplicate variants."""
    first = client.get(
        "/variants",
        params={"limit": 10, "offset": 0},
    )
    second = client.get(
        "/variants",
        params={"limit": 10, "offset": 10},
    )

    assert first.status_code == 200
    assert second.status_code == 200

    first_results = first.json()["results"]
    second_results = second.json()["results"]

    assert len(first_results) == 10
    assert len(second_results) == 10

    key = lambda v: (v["chrom"], v["pos"], v["ref"], v["alt"])
    first_keys = {key(v) for v in first_results}
    second_keys = {key(v) for v in second_results}

    assert first_keys.isdisjoint(second_keys)


def test_no_matching_variant(client):
    """An unknown gene should return an empty result."""
    response = client.get(
        "/variants",
        params={"gene": "__NO_SUCH_GENE__"},
    )

    assert response.status_code == 200
    assert response.json()["count"] == 0
    assert response.json()["results"] == []


def test_invalid_coordinate_range(client):
    """The API should reject a start position greater than the end."""
    response = client.get(
        "/variants",
        params={
            "chrom": "chr1",
            "start": 100000,
            "end": 100,
        },
    )

    assert response.status_code == 422


def test_invalid_frequency_range(client):
    """The API should reject an AF minimum greater than its maximum."""
    response = client.get(
        "/variants",
        params={
            "af_min": 0.8,
            "af_max": 0.2,
        },
    )

    assert response.status_code == 422


def test_limit_cannot_exceed_500(client):
    """The API should reject limits greater than 500."""
    response = client.get(
        "/variants",
        params={"limit": 501},
    )

    assert response.status_code == 422
