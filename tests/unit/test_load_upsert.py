from __future__ import annotations

from port_analytics.load.upsert import (
    build_cargo_type_upsert,
    build_flag_upsert,
    build_port_merge_link,
    build_port_upsert,
    build_throughput_upsert,
)
from port_analytics.models import (
    CargoType,
    DataQualityFlag,
    Direction,
    FlagType,
    Port,
    PortThroughputRow,
    ThroughputRef,
)


def test_build_port_upsert_merges_on_eurostat_code() -> None:
    port = Port(eurostat_code="DE_1DEHAM", port_name="Hamburg", country_code="DE")

    stmt = build_port_upsert(port)

    assert "MERGE dbo.ports" in stmt.sql
    assert "ON target.eurostat_code = src.eurostat_code" in stmt.sql
    assert stmt.params == (
        "DE_1DEHAM",
        "Hamburg",
        "DE",
        None,
        "Hamburg",
        "DE",
        None,
        "DE_1DEHAM",
    )


def test_build_port_merge_link_targets_the_right_row() -> None:
    stmt = build_port_merge_link("BE_0BEANR", 7)

    assert "UPDATE dbo.ports SET merged_into_port_id" in stmt.sql
    assert stmt.params == (7, "BE_0BEANR")


def test_build_cargo_type_upsert_merges_on_code() -> None:
    cargo_type = CargoType(cargo_type_code="LBK", cargo_type_name="Liquid bulk")

    stmt = build_cargo_type_upsert(cargo_type)

    assert "MERGE dbo.cargo_types" in stmt.sql
    assert stmt.params == ("LBK", "Liquid bulk", "Liquid bulk", "LBK")


def test_build_throughput_upsert_merges_on_the_full_natural_key() -> None:
    row = PortThroughputRow(
        port_code="DE_1DEHAM",
        cargo_type_code="TOTAL",
        year=2021,
        direction=Direction.TOTAL,
        gross_weight_tonnes=111156000.0,
        source="mar_mg_aa_pwhd",
    )

    stmt = build_throughput_upsert(row, port_id=4, cargo_type_id=1)

    assert "MERGE dbo.port_throughput" in stmt.sql
    assert "target.port_id = src.port_id" in stmt.sql
    assert "target.source = src.source" in stmt.sql
    assert "OUTPUT" in stmt.sql
    assert "$action" in stmt.sql
    assert stmt.params == (
        4,
        1,
        2021,
        "total",
        "mar_mg_aa_pwhd",
        111156000.0,
        4,
        1,
        2021,
        "total",
        111156000.0,
        "mar_mg_aa_pwhd",
    )


def test_build_flag_upsert_is_null_safe_for_optional_fks() -> None:
    flag = DataQualityFlag(
        flag_type=FlagType.PORT_MERGER,
        port_code="BE_0BEANR",
        description="Antwerpen merged into Antwerp-Bruges from 2022.",
        resolution="Linked via ports.merged_into_port_id.",
    )

    stmt = build_flag_upsert(flag, port_id=2, throughput_id=None)

    assert "IS NULL AND src.throughput_id IS NULL" in stmt.sql
    assert "IS NULL AND src.port_id IS NULL" in stmt.sql
    assert stmt.params == (
        None,
        2,
        "port_merger",
        "Antwerpen merged into Antwerp-Bruges from 2022.",
        None,
        2,
        "port_merger",
        "Antwerpen merged into Antwerp-Bruges from 2022.",
        "Linked via ports.merged_into_port_id.",
    )


def test_build_flag_upsert_carries_a_throughput_id_when_given() -> None:
    flag = DataQualityFlag(
        flag_type=FlagType.MISSING_YEAR,
        description="No data reported.",
        resolution="Left absent.",
        throughput_ref=ThroughputRef(
            port_code="DE_1DEHAM", cargo_type_code="RO_MNSP", year=2015, direction=Direction.TOTAL
        ),
    )

    stmt = build_flag_upsert(flag, port_id=None, throughput_id=99)

    assert stmt.params[0] == 99
    assert stmt.params[4] == 99
