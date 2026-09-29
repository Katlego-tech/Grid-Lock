"""The migration and the domain model's class diagram, checked against each other.

Both directions: every field in the diagram exists with the stated type and nullability,
and the database has no table or column the diagram doesn't account for.
"""

from __future__ import annotations

import psycopg
import pytest

import domain_model

# Diagram class -> table. Coordinates is a value type (a PostGIS point) and EvidenceChunk a
# value stored inside TriageResult.evidence; neither is a table.
TABLES = {
    "Report": "reports",
    "TriageResult": "triage_results",
    "Incident": "incidents",
    "Landmark": "landmarks",
}
VALUE_TYPES = {"Coordinates", "EvidenceChunk"}

# Bookkeeping that is not domain data: migrate.sh's record of applied migrations.
INFRA_TABLES = {"schema_migrations"}

# Diagram type -> column type, as information_schema reports it (udt_name for PostGIS,
# enum and array types, data_type otherwise).
COLUMN_TYPES = {
    "UUID": "uuid",
    "str": "text",
    "int": "integer",
    "float": "double precision",
    "datetime": "timestamp with time zone",
    "Coordinates": "geography",
    "Tier": "tier",
    "ReportState": "report_state",
    "LocationConfidence": "location_confidence",
    "IncidentState": "incident_state",
    "List~str~": "_text",
    # A list of value objects, kept with the result it justified (see 0001_init.sql).
    "List~EvidenceChunk~": "jsonb",
}


def _columns(db: psycopg.Connection) -> dict[str, dict[str, tuple[str, bool]]]:
    rows = db.execute(
        """
        SELECT c.table_name, c.column_name,
               CASE WHEN c.data_type IN ('USER-DEFINED', 'ARRAY') THEN c.udt_name
                    ELSE c.data_type END,
               c.is_nullable = 'YES'
          FROM information_schema.columns c
          JOIN information_schema.tables t USING (table_schema, table_name)
         WHERE c.table_schema = 'public'
           AND t.table_type = 'BASE TABLE'
           AND c.table_name <> 'spatial_ref_sys'   -- PostGIS's own catalogue
        """
    ).fetchall()
    tables: dict[str, dict[str, tuple[str, bool]]] = {}
    for table, column, col_type, nullable in rows:
        tables.setdefault(table, {})[column] = (col_type, nullable)
    return tables


def _expected() -> dict[str, dict[str, tuple[str, bool]]]:
    """Table -> column -> (type, nullable), derived from the diagram."""
    classes = domain_model.classes()
    assert set(classes) == set(TABLES) | VALUE_TYPES, (
        "the class diagram's classes changed -- map the new class in TABLES or VALUE_TYPES"
    )
    expected: dict[str, dict[str, tuple[str, bool]]] = {t: {} for t in TABLES.values()}
    for cls, table in TABLES.items():
        for field in classes[cls]:
            assert field.type in COLUMN_TYPES, (
                f"{cls}.{field.name}: no column type for {field.type}"
            )
            expected[table][field.name] = (COLUMN_TYPES[field.type], field.optional)
    return expected


def test_database_has_exactly_the_diagrams_tables(db: psycopg.Connection) -> None:
    assert set(_columns(db)) == set(TABLES.values()) | INFRA_TABLES


@pytest.mark.parametrize("table", sorted(TABLES.values()))
def test_columns_match_the_diagram_both_ways(db: psycopg.Connection, table: str) -> None:
    actual = _columns(db)[table]
    expected = _expected()[table]

    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    assert not missing, f"{table}: in the diagram but not the database: {missing}"
    assert not extra, f"{table}: in the database but not the diagram: {extra}"
    wrong = {c: (actual[c], expected[c]) for c in expected if actual[c] != expected[c]}
    assert not wrong, f"{table}: (actual, expected) (type, nullable) differ: {wrong}"


@pytest.mark.parametrize(
    ("table", "column"),
    [("landmarks", "grid_cell"), ("incidents", "grid_cell"), ("triage_results", "grid_cell")],
)
def test_every_grid_cell_is_an_h3_cell(db: psycopg.Connection, table: str, column: str) -> None:
    row = db.execute(
        "SELECT domain_name FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
        (table, column),
    ).fetchone()
    assert row == ("h3_cell",)


def test_coordinates_are_wgs84_points(db: psycopg.Connection) -> None:
    rows = db.execute(
        "SELECT f_table_name, f_geography_column, type, srid FROM geography_columns"
    ).fetchall()
    assert rows, "no geography columns found"
    for table, column, geo_type, srid in rows:
        assert (geo_type, srid) == ("Point", 4326), f"{table}.{column} is {geo_type}/{srid}"


@pytest.mark.parametrize(
    ("pg_type", "doc_enum"),
    [
        ("tier", "Tier"),
        ("report_state", "ReportState"),
        ("location_confidence", "LocationConfidence"),
    ],
)
def test_enum_values_match_the_contracts(
    db: psycopg.Connection, pg_type: str, doc_enum: str
) -> None:
    labels = [
        r[0]
        for r in db.execute(
            "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid"
            " WHERE t.typname = %s ORDER BY e.enumsortorder",
            (pg_type,),
        )
    ]
    assert labels == domain_model.python_enums()[doc_enum]


def test_incident_state_values_match_the_document(db: psycopg.Connection) -> None:
    labels = [
        r[0]
        for r in db.execute(
            "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid"
            " WHERE t.typname = 'incident_state' ORDER BY e.enumsortorder"
        )
    ]
    assert labels == domain_model.incident_states()
