"""
Validation result data structures.
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class IssueSeverity(Enum):
    """Severity levels for validation issues."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationIssue:
    """A single validation issue."""
    severity: IssueSeverity
    message: str
    code: str  # e.g., "QPDK001", "LUACHECK-W611"
    line: int | None = None
    column: int | None = None
    source: str = "qpdk"  # "qpdk", "luacheck", "lupa"

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "message": self.message,
            "code": self.code,
            "line": self.line,
            "column": self.column,
            "source": self.source,
        }

    def format_location(self) -> str:
        if self.line is not None:
            if self.column is not None:
                return f":{self.line}:{self.column}"
            return f":{self.line}"
        return ""


@dataclass
class ValidationResult:
    """Complete validation result for a plugin."""
    plugin_path: str
    valid: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)
    plugin_info: dict[str, Any] | None = None
    properties_count: int = 0
    controls_count: int = 0
    pages_count: int = 0

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == IssueSeverity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == IssueSeverity.WARNING]

    @property
    def infos(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == IssueSeverity.INFO]

    def add_error(self, code: str, message: str, line: int | None = None,
                  column: int | None = None, source: str = "qpdk"):
        self.issues.append(ValidationIssue(
            severity=IssueSeverity.ERROR,
            message=message,
            code=code,
            line=line,
            column=column,
            source=source,
        ))
        self.valid = False

    def add_warning(self, code: str, message: str, line: int | None = None,
                    column: int | None = None, source: str = "qpdk"):
        self.issues.append(ValidationIssue(
            severity=IssueSeverity.WARNING,
            message=message,
            code=code,
            line=line,
            column=column,
            source=source,
        ))

    def add_info(self, code: str, message: str, line: int | None = None,
                 column: int | None = None, source: str = "qpdk"):
        self.issues.append(ValidationIssue(
            severity=IssueSeverity.INFO,
            message=message,
            code=code,
            line=line,
            column=column,
            source=source,
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_path": self.plugin_path,
            "valid": self.valid,
            "summary": {
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "info": len(self.infos),
            },
            "plugin_info": self.plugin_info,
            "structure": {
                "properties": self.properties_count,
                "controls": self.controls_count,
                "pages": self.pages_count,
            },
            "issues": [i.to_dict() for i in self.issues],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_tap(self) -> str:
        """Format as TAP (Test Anything Protocol) output."""
        lines = ["TAP version 14", f"1..{len(self.issues) + 1}"]

        # Overall plugin validity
        if self.valid:
            lines.append("ok 1 - Plugin structure valid")
        else:
            lines.append("not ok 1 - Plugin structure invalid")

        # Individual issues
        for i, issue in enumerate(self.issues, start=2):
            status = "not ok" if issue.severity == IssueSeverity.ERROR else "ok"
            loc = issue.format_location()
            lines.append(f"{status} {i} - [{issue.code}] {issue.message}{loc}")
            if issue.severity == IssueSeverity.ERROR:
                lines.append("  ---")
                lines.append(f"  severity: {issue.severity.value}")
                lines.append(f"  source: {issue.source}")
                lines.append("  ...")

        return "\n".join(lines)

    def print_rich(self, console, show_warnings: bool = True) -> None:
        """Print formatted output using rich."""
        from rich.panel import Panel
        from rich.table import Table

        # Header
        if self.valid:
            console.print(Panel(f"[green]VALID[/green] {self.plugin_path}",
                                title="Plugin Validation"))
        else:
            console.print(Panel(f"[red]INVALID[/red] {self.plugin_path}",
                                title="Plugin Validation"))

        # Plugin info
        if self.plugin_info:
            info_table = Table(title="Plugin Info", show_header=False)
            info_table.add_column("Field", style="cyan")
            info_table.add_column("Value")
            for key, value in self.plugin_info.items():
                info_table.add_row(key, str(value))
            console.print(info_table)

        # Issues
        if self.issues:
            issue_table = Table(title="Issues")
            issue_table.add_column("Severity", width=8)
            issue_table.add_column("Code", width=12)
            issue_table.add_column("Line", width=6)
            issue_table.add_column("Message")

            for issue in self.issues:
                if issue.severity == IssueSeverity.ERROR:
                    sev_style = "[red]ERROR[/red]"
                elif issue.severity == IssueSeverity.WARNING:
                    if not show_warnings:
                        continue
                    sev_style = "[yellow]WARN[/yellow]"
                else:
                    sev_style = "[blue]INFO[/blue]"

                line_str = str(issue.line) if issue.line else "-"
                issue_table.add_row(sev_style, issue.code, line_str, issue.message)

            console.print(issue_table)

        # Summary
        console.print(f"\n[bold]Summary:[/bold] {len(self.errors)} errors, "
                      f"{len(self.warnings)} warnings, {len(self.infos)} info")
