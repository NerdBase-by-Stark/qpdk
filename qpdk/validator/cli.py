"""
Standalone CLI for Plugin Validator
"""

import click
from rich.console import Console

from .validator import PluginValidator

console = Console()


@click.command()
@click.argument("plugin_paths", nargs=-1, type=click.Path(exists=True, dir_okay=False))
@click.option("--format", "-f", "output_format",
              type=click.Choice(["text", "json", "tap"]),
              default="text",
              help="Output format")
@click.option("--strict", is_flag=True, help="Treat warnings as errors")
@click.option("--qsys-version", type=click.Choice(["7", "9"]), default="9",
              help="Target Q-SYS version")
@click.option("--ruleset", "-r", "ruleset",
              type=click.Choice(["P0", "default", "full"], case_sensitive=False),
              default="default",
              help="Rule set to apply: P0 (crash-preventing only), "
                   "default (ERROR+WARNING, used when not given), "
                   "full (adds INFO advisories)")
@click.option("--quiet", "-q", is_flag=True, help="Only show errors")
def main(plugin_paths: tuple[str, ...], output_format: str, strict: bool,
         qsys_version: str, ruleset: str, quiet: bool):
    """Validate Q-SYS plugin files.

    Examples:
        qpdk-validate plugin.qplug
        qpdk-validate *.qplug --format json
        qpdk-validate plugin.qplug --strict
    """
    if not plugin_paths:
        console.print("[yellow]No plugin files specified[/yellow]")
        console.print("Usage: qpdk-validate [OPTIONS] PLUGIN_PATHS...")
        raise SystemExit(1)

    validator = PluginValidator(qsys_version=qsys_version, ruleset=ruleset)
    all_valid = True
    results = []

    for path in plugin_paths:
        result = validator.validate_file(path)
        if ruleset.lower() != "full":
            result.hide_info()
        results.append(result)

        if not result.valid:
            all_valid = False

        if output_format == "json":
            # Will print all at end
            pass
        elif output_format == "tap":
            # Machine formats bypass rich: FORCE_COLOR would inject ANSI (D7).
            click.echo(result.to_tap())
        else:
            result.print_rich(console, show_warnings=not quiet)
            console.print()

    if output_format == "json":
        import json
        output = {
            "total": len(results),
            "valid": sum(1 for r in results if r.valid),
            "invalid": sum(1 for r in results if not r.valid),
            "results": [r.to_dict() for r in results],
        }
        click.echo(json.dumps(output, indent=2))

    # Exit codes
    if not all_valid:
        raise SystemExit(1)
    if strict and any(r.warnings for r in results):
        raise SystemExit(2)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
