"""jobscout CLI. Phases 0-2: config, search, fetch. (assess/report land later.)"""

from __future__ import annotations

import typer

from . import db
from .config import Config, load, resolve_window
from .fetch import run_fetch
from .sources import get_source

app = typer.Typer(add_completion=False, help="Fetch, score, and shortlist job postings.")


def _cfg() -> Config:
    from .config import ConfigError

    try:
        return load()
    except ConfigError as e:
        typer.secho(f"config error: {e}", fg="red", err=True)
        raise typer.Exit(1) from e


@app.command()
def config() -> None:
    """Show the resolved config: active queries, locations, and time window."""
    cfg = _cfg()
    conn = db.connect(cfg.db_path)
    last_run = db.last_successful_run_iso(conn, cfg.source)
    window = resolve_window(cfg, None, last_run)

    typer.echo(f"mode:        {cfg.mode}")
    typer.echo(f"source:      {cfg.source}")
    typer.echo(f"model:       {cfg.model}")
    typer.echo(f"profile_dir: {cfg.profile_dir}")
    typer.echo(f"output_dir:  {cfg.output_dir}")
    typer.echo(f"db:          {cfg.db_path}")
    typer.echo(f"last run:    {last_run or '(none — next run backfills 30d)'}")
    typer.echo(f"window:      {window}")
    typer.echo(f"\nlocations ({len(cfg.locations)}):")
    for loc in cfg.locations:
        wt = {1: "on-site", 2: "remote", 3: "hybrid"}.get(loc.work_type or 0, "any")
        typer.echo(f"  - {loc.label}  (geoId={loc.geo_id}, work_type={wt})")
    typer.echo(f"\nqueries ({len(cfg.queries)}):")
    for i, q in enumerate(cfg.queries, 1):
        typer.echo(f"  {i:2d}. {q}")


@app.command()
def search(
    query: str = typer.Argument(..., help="Raw keywords string to search."),
    location: str = typer.Option("Toronto, Ontario, Canada", "--location", "-l"),
    since: str = typer.Option("30d", "--since", help="24h | 7d | 30d"),
    limit: int = typer.Option(25, "--limit", "-n"),
) -> None:
    """Ad-hoc one-off search. Prints results, does NOT touch the DB or run state."""
    cfg = _cfg()
    from .config import Location

    src = get_source(cfg.source)
    window = resolve_window(cfg, since, None)
    loc = Location(label=location, geo_id=None)
    object.__setattr__(cfg, "max_results_per_query", limit)

    results = src.fetch(query, loc, window, cfg)
    typer.echo(f"{len(results)} results for {query!r} @ {location} (window={window})\n")
    for p in results:
        typer.echo(f"  {p.title}")
        typer.echo(f"    {p.company}  ·  {p.location}  ·  {p.posted_at or '?'}")
        typer.echo(f"    {p.url}")
    if hasattr(src, "close"):
        src.close()


@app.command()
def fetch(
    since: str | None = typer.Option(None, "--since", help="Force window: 24h | 7d | 30d"),
    skip_descriptions: bool = typer.Option(False, "--skip-descriptions"),
) -> None:
    """Run the configured queries, store postings + descriptions in the DB."""
    cfg = _cfg()
    conn = db.connect(cfg.db_path)
    result = run_fetch(conn, cfg, since=since, skip_descriptions=skip_descriptions)
    typer.echo(
        f"\ndone: window={result.window}  fetched={result.fetched}  "
        f"unique={result.unique}  new={result.new}  descriptions={result.descriptions}"
        + ("  [PARTIAL]" if result.partial else "")
    )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Run the local web backend (FastAPI). Localhost only by default."""
    import uvicorn

    uvicorn.run("jobscout.web.app:app", host=host, port=port, reload=reload)


@app.command()
def runs(limit: int = typer.Option(10, "--limit", "-n")) -> None:
    """Show recent run history."""
    cfg = _cfg()
    conn = db.connect(cfg.db_path)
    rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    if not rows:
        typer.echo("no runs yet")
        return
    for r in rows:
        typer.echo(
            f"#{r['id']:<3} {r['started_at']}  {r['status'] or '...':7s}  "
            f"win={r['time_posted_used']}  fetched={r['n_fetched']} unique={r['n_unique']} "
            f"new={r['n_new']}"
        )


if __name__ == "__main__":
    app()
