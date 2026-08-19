"""Single entrypoint: ingest -> transform -> load. Run as:

port-analytics
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from port_analytics.config import DATASET_CODES, DEFAULT_LANDING_DIR
from port_analytics.ingest.pull import pull_and_land
from port_analytics.load.connection import connect
from port_analytics.load.loader import load_all
from port_analytics.transform.pipeline import transform_all

app = typer.Typer(add_completion=False)


@app.command()
def run(landing_dir: Path = DEFAULT_LANDING_DIR) -> None:
    """Pull both Eurostat datasets, transform them, and load into Azure SQL."""
    typer.echo("Pulling raw data from Eurostat...")
    landed_paths = pull_and_land(landing_dir=landing_dir)
    for path in landed_paths:
        typer.echo(f"  landed {path}")

    payloads = {
        dataset_code: json.loads(
            next(p for p in landed_paths if p.name.startswith(dataset_code)).read_text(
                encoding="utf-8"
            )
        )
        for dataset_code in DATASET_CODES.values()
    }

    typer.echo("Transforming...")
    rows, flags = transform_all(
        payloads[DATASET_CODES["goods_by_direction"]],
        payloads[DATASET_CODES["goods_by_cargo_type"]],
    )
    typer.echo(f"  {len(rows)} throughput rows, {len(flags)} data-quality flags")

    typer.echo("Loading into Azure SQL...")
    with connect() as conn:
        summary = load_all(conn, rows, flags)

    typer.echo(
        f"Loaded: {summary.ports_loaded} ports, {summary.cargo_types_loaded} cargo types, "
        f"{summary.throughput_rows_loaded} throughput rows, {summary.flags_loaded} flags "
        f"({summary.revisions_detected} revisions detected this run)."
    )


if __name__ == "__main__":
    app()
