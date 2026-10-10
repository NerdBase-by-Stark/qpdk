"""
QPDK Main CLI Entry Point

Provides unified access to all QPDK tools.
"""

import json
from typing import Any

import click
from rich.console import Console

from . import __version__

console = Console()


@click.group()
@click.version_option(version=__version__, prog_name="qpdk")
def main():
    """Q-SYS Plugin Development Kit (QPDK)

    Tools for developing, validating, and testing Q-SYS plugins without Q-SYS Designer.
    """
    pass


@main.command()
@click.argument("plugin_paths", nargs=-1, required=True,
                type=click.Path(exists=True, dir_okay=False))
@click.option("--format", "-f", "output_format",
              type=click.Choice(["text", "json", "tap"]),
              default="text",
              help="Output format for validation results")
@click.option("--strict", is_flag=True, help="Treat warnings as errors")
@click.option("--qsys-version", type=click.Choice(["7", "9"]), default="9",
              help="Target Q-SYS version for API validation")
@click.option("--ruleset", "-r", "ruleset",
              type=click.Choice(["P0", "default", "full"], case_sensitive=False),
              default=None,
              help="Rule set to apply: P0 (crash-preventing rules only), "
                   "default (crash-preventing and quality rules, INFO listed), "
                   "full (every rule, INFO listed). When omitted, every rule "
                   "runs and INFO advisories are counted but not listed.")
@click.option("--quiet", "-q", is_flag=True,
              help="Only show errors in text output")
def validate(plugin_paths: tuple[str, ...], output_format: str, strict: bool,
             qsys_version: str, ruleset: str | None, quiet: bool):
    """Validate one or more Q-SYS plugin files.

    PLUGIN_PATHS: One or more .qplug files. Passing multiple files reuses a
    single validator instance, which enables cross-file checks such as
    QPDK008 (duplicate plugin Id).

    \b
    Rulesets:
      (none)  Every rule runs; errors and warnings listed, INFO counted
      P0      Only crash-preventing rules
      default Crash-preventing and quality rules, INFO listed
      full    Every rule, INFO advisories listed

    --ruleset default is not the same as leaving the flag off: it skips the
    advisory rules and lists INFO rows. Run `qpdk rules` to see each rule's
    rulesets.

    Exit codes: 0 = no errors, 1 = at least one error, 2 = warnings under
    --strict, or a usage error.
    """
    from .validator import PluginValidator

    # One validator instance across all files so cross-file state (QPDK008
    # duplicate-Id detection) actually has something to compare against.
    hide_info = ruleset is None
    validator = PluginValidator(qsys_version=qsys_version, ruleset=ruleset or "full")
    results = [validator.validate_file(p) for p in plugin_paths]
    if hide_info:
        for r in results:
            r.hide_info()

    # Machine formats bypass rich entirely: rich honors FORCE_COLOR even when
    # stdout is redirected, which injected ANSI escapes into JSON/TAP output
    # and broke every downstream parser.
    if output_format == "json":
        if len(results) == 1:
            # Preserve the single-file JSON contract (one result object).
            click.echo(results[0].to_json())
        else:
            click.echo(json.dumps({
                "total": len(results),
                "valid": sum(1 for r in results if r.valid),
                "invalid": sum(1 for r in results if not r.valid),
                "results": [r.to_dict() for r in results],
            }, indent=2))
    elif output_format == "tap":
        for result in results:
            click.echo(result.to_tap())
    else:
        for result in results:
            result.print_rich(console, show_warnings=not quiet, strict=strict)
            console.print()

    # Exit code: 0 = success, 1 = errors, 2 = warnings (strict mode)
    if any(r.errors for r in results):
        raise SystemExit(1)
    if strict and any(r.warnings for r in results):
        raise SystemExit(2)


@main.command()
@click.option("--port", "-p", default=1710, show_default=True, help="Port to listen on")
@click.option("--host", "-h", "host", default="127.0.0.1", show_default=True,
              help="Host to bind to")
@click.option("--record", "-r", type=click.Path(), help="Record responses to file")
@click.option("--playback", type=click.Path(exists=True), help="Playback recorded responses")
def qrc(port: int, host: str, record: str | None, playback: str | None):
    """Start a mock QRC protocol server.

    Simulates a Q-SYS Core for integration testing.
    """
    from .qrc_tester import QRCMockServer

    server = QRCMockServer(host=host, port=port)

    if playback:
        server.load_playback(playback)

    console.print(f"[green]Starting QRC Mock Server on {host}:{port}[/green]")
    console.print("Press Ctrl+C to stop")

    try:
        server.run(record_to=record)
    except KeyboardInterrupt:
        console.print("\n[yellow]Server stopped[/yellow]")


@main.command(name="design-check")
@click.argument("plugin_paths", nargs=-1, required=True,
                type=click.Path(exists=True, dir_okay=False))
@click.option("--format", "-f", "output_format",
              type=click.Choice(["text", "json"]),
              default="text",
              help="Output format")
def design_check(plugin_paths: tuple[str, ...], output_format: str):
    """Evaluate plugins the way Designer does at drag-in.

    Runs each plugin in the mock runtime's design-time mode (Controls nil)
    and calls its definition functions (GetProperties/GetControls/
    GetControlLayout/...). Catches the crashes static rules can only
    approximate. A nonzero exit means the plugin would crash Q-SYS Designer
    when dragged into a schematic.

    Also prints the layout findings QPDK040 to QPDK046 (overlaps, zero sizes,
    off-page controls). They are advisory and do not change the exit code.

    Exit codes: 0 = no design-time errors, 1 = a plugin would crash Designer,
    2 = usage error.
    """
    from pathlib import Path as _Path

    from .mock_runtime.runtime import QSYSRuntime
    from .validator.ui_lint import lint_report

    results: list[dict[str, Any]] = []
    for path in plugin_paths:
        raw = _Path(path).read_bytes()
        try:
            code = raw.decode("utf-8")
        except UnicodeDecodeError:
            code = raw.decode("latin-1")
        report = QSYSRuntime(design_time=True).run_design_time(code)
        # UI-LINT (QPDK040-046) over every page that evaluated cleanly;
        # crashed pages are already reported as errors above; geometry
        # findings are advisory (WARNING ceiling) and never flip the exit.
        results.append({
            "plugin": path,
            "errors": list(report.errors),
            "ui_findings": lint_report(report),
        })

    clean = all(not r["errors"] for r in results)

    if output_format == "json":
        # Plain stdout, not rich: machine output must stay ANSI-free.
        click.echo(json.dumps({"clean": clean, "results": results}, indent=2))
    else:
        for r in results:
            if r["errors"]:
                console.print(f"[red]FAIL[/red] {r['plugin']}: would crash Designer at drag-in:")
                for e in r["errors"]:
                    click.echo(f"  {e}")
            else:
                console.print(f"[green]OK[/green]   {r['plugin']}")
            for f in r["ui_findings"]:
                click.echo(
                    f"  {f['code']} [{f['severity']}] page {f['page']} "
                    f"{f['name']}: {f['message']}")

    raise SystemExit(0 if clean else 1)


@main.command()
@click.option("--format", "-f", "output_format",
              type=click.Choice(["table", "json"]),
              default="table",
              help="Output format for the rule catalogue")
def rules(output_format: str):
    """List every validator rule with its severity, rulesets and description.

    Use --format json for the full, untruncated descriptions.
    """
    from .validator.rulesets import RULE_METADATA, RULESETS

    catalog = []
    for code in sorted(RULE_METADATA):
        meta = RULE_METADATA[code]
        catalog.append({
            "code": code,
            "severity": meta["severity"].value,
            "phase": meta["phase"],
            "rulesets": sorted(name for name, codes in RULESETS.items() if code in codes),
            "category": meta["category"],
            "description": meta["description"],
        })

    if output_format == "json":
        # Plain stdout, not rich: machine output must stay ANSI-free.
        click.echo(json.dumps({"rules": catalog}, indent=2))
        return

    from rich.markup import escape
    from rich.table import Table

    table = Table(title=f"QPDK validator rules ({len(catalog)})",
                  caption="Phase: 1 = crash-preventing, 2 = quality, 3 = advisory")
    table.add_column("Code", no_wrap=True)
    table.add_column("Severity")
    table.add_column("Phase", justify="center")
    table.add_column("Rulesets")
    table.add_column("Category")
    table.add_column("Description")
    for entry in catalog:
        # escape(): descriptions contain text such as "[i]" that rich would
        # otherwise read as markup and drop from the table.
        table.add_row(entry["code"], entry["severity"], str(entry["phase"]),
                      ",".join(entry["rulesets"]), entry["category"],
                      escape(entry["description"]))
    console.print(table)


def _find_test_dir(plugin_path: str) -> str | None:
    """Locate a plugin's pytest directory.

    Checks `test/` and `tests/` as siblings of the plugin file, then walks
    up to the enclosing repo root (directory containing `.git`) and checks
    `test/` / `tests/` there. Returns None if nothing is found.
    """
    from pathlib import Path

    plugin = Path(plugin_path).resolve()
    start = plugin.parent if plugin.is_file() else plugin

    candidates = [start / "test", start / "tests"]
    current = start
    while True:
        if (current / ".git").exists():
            candidates.extend([current / "test", current / "tests"])
            break
        if current.parent == current:
            break
        current = current.parent

    for candidate in candidates:
        if candidate.is_dir():
            return str(candidate)
    return None


@main.command()
@click.argument("plugin_path", type=click.Path(exists=True))
@click.option("--test-dir", "-t", type=click.Path(), help="Directory containing test files")
@click.option("--coverage", is_flag=True, help="Generate coverage report")
def test(plugin_path: str, test_dir: str | None, coverage: bool):
    """Run a plugin's pytest suite.

    Locates the test directory (--test-dir, else `test/` or `tests/` next to
    the plugin file or in its repo root), runs pytest there, and exits with
    pytest's exit code. Exits with code 2 if no test directory is found:
    a plugin with no tests is not a passing plugin.
    """
    import importlib.util
    import subprocess
    import sys
    from pathlib import Path

    resolved_dir: str | None
    if test_dir:
        if not Path(test_dir).is_dir():
            console.print(f"[red]Error: --test-dir not found: {test_dir}[/red]")
            raise SystemExit(2)
        resolved_dir = test_dir
    else:
        resolved_dir = _find_test_dir(plugin_path)
        if resolved_dir is None:
            console.print(
                f"[red]Error: no test directory found for {plugin_path}.[/red]\n"
                "Looked for test/ or tests/ next to the plugin and in its repo "
                "root. Pass --test-dir explicitly, or write tests."
            )
            raise SystemExit(2)
        if Path(resolved_dir).resolve().parent != Path(plugin_path).resolve().parent:
            console.print(
                f"[yellow]Warning: test dir {resolved_dir} found via repo-root "
                "fallback - it may not belong to this plugin. Pass --test-dir "
                "to be explicit.[/yellow]"
            )

    cmd = [sys.executable, "-m", "pytest", resolved_dir]
    if coverage:
        if importlib.util.find_spec("pytest_cov") is not None:
            cmd.append("--cov")
        else:
            console.print("[yellow]Note: pytest-cov not installed; ignoring --coverage[/yellow]")

    console.print(f"[cyan]Running: {' '.join(cmd)}[/cyan]")
    result = subprocess.run(cmd)  # streams output; no capture
    raise SystemExit(result.returncode)



@main.command(name="qsc-eval")
@click.argument("plugin_paths", nargs=-1, required=True,
                type=click.Path(exists=True, dir_okay=False))
@click.option("--format", "-f", "output_format",
              type=click.Choice(["text", "json"]),
              default="text",
              help="Output format")
@click.option("--maxprops", is_flag=True,
              help="Evaluate with all booleans true and numerics at Max (upstream -maxprops)")
@click.option("--prop", "-p", "props", multiple=True, metavar="NAME=VALUE",
              help="Override a property value for evaluation (repeatable)")
@click.option("--exceptions", "exceptions_path", type=click.Path(exists=True),
              help="PluginEvalConfig.json with an Exceptions block (upstream format)")
@click.option("--strict", is_flag=True, help="Treat warnings as errors")
def qsc_eval(plugin_paths: tuple[str, ...], output_format: str, maxprops: bool,
             props: tuple[str, ...], exceptions_path: str | None, strict: bool):
    """Run QSC's official Plugin-Evaluation-Script (vendored, MIT).

    Executes the plugin's design-time code with Lua 5.3 (the interpreter the
    upstream repo states Q-SYS Designer uses) and checks the returned
    Properties, Controls and Layout tables against the official SDK
    vocabulary, reserved names, PluginInfo conventions, and the match between
    layout and controls. It complements `qpdk validate`: QSC's script checks
    the design-time output, QPDK's rules check the source and runtime, and it
    sees loop-generated control names that static checks cannot.

    Exit codes: 0 = no QSC errors, 1 = errors (or warnings with --strict),
    2 = the plugin's design-time code crashed under Lua 5.3, or a usage error.
    """
    from .qsc_eval import run_qsc_eval

    overrides = {}
    for item in props:
        name, _, value = item.partition("=")
        if not _:
            raise click.BadParameter(f"--prop needs NAME=VALUE, got {item!r}")
        overrides[name] = value

    crashed = False
    failed = False
    payload = []
    for path in plugin_paths:
        r = run_qsc_eval(path, prop_overrides=overrides or None,
                         maxprops=maxprops, exceptions_path=exceptions_path)
        payload.append(r.to_dict())
        crashed = crashed or r.crash is not None
        failed = failed or bool(r.errors) or (strict and bool(r.warnings))

        if output_format == "text":
            if r.crash:
                console.print(f"[red]CRASH[/red] {path}: design-time code failed under Lua 5.3:")
                click.echo(f"  {r.crash}")
                continue
            state = "[red]FAIL[/red]" if r.errors else "[green]OK[/green]  "
            console.print(f"{state} {path}: {len(r.errors)} error(s), "
                          f"{len(r.warnings)} warning(s), {len(r.notices)} notice(s)")
            for e in r.errors:
                click.echo(f"  ERROR   {e}")
            for w in r.warnings:
                click.echo(f"  WARNING {w}")
            for n in r.notices:
                click.echo(f"  notice  {n}")

    if output_format == "json":
        # Plain stdout, not rich: machine output must stay ANSI-free.
        click.echo(json.dumps({"ok": not (crashed or failed), "results": payload}, indent=2))

    raise SystemExit(2 if crashed else (1 if failed else 0))


if __name__ == "__main__":
    main()
