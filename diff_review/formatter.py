import json
import sys
from datetime import date
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from .schema import ReviewOutput

console = Console()

_VERDICT_STYLE = {
    "approve": "bold green",
    "request_changes": "bold red",
    "needs_discussion": "bold yellow",
}

_SEVERITY_STYLE = {
    "high": "red",
    "medium": "yellow",
    "low": "blue",
}

_SEVERITY_EMOJI = {
    "high": "🔴",
    "medium": "🟡",
    "low": "🔵",
}


def format_json(output: ReviewOutput) -> str:
    return json.dumps(output.model_dump(), indent=2)


def format_markdown(output: ReviewOutput, source: str = "") -> str:
    verdict_display = output.verdict.upper().replace("_", " ")
    lines = [f"# diff-review: {verdict_display}\n"]

    meta = [f"**Date:** {date.today()}"]
    if source:
        meta.append(f"**Source:** {source}")
    lines.append("  \n".join(meta))
    lines.append("\n---\n")
    lines.append(f"## Summary\n\n{output.summary}\n")

    if output.issues:
        lines.append(f"---\n\n## Issues ({len(output.issues)} found)\n")
        for issue in output.issues:
            emoji = _SEVERITY_EMOJI.get(issue.severity, "⚪")
            lines.append(f"### {emoji} {issue.severity.upper()} — `{issue.file}`\n")
            lines.append(f"**Issue:** {issue.description}  ")
            lines.append(f"**Suggestion:** {issue.suggestion}  ")
            if issue.evidence:
                lines.append(f"**Evidence:**\n```\n{issue.evidence}\n```")
            lines.append("")

    if output.highlights:
        lines.append("---\n\n## Highlights\n")
        for h in output.highlights:
            lines.append(f"- ✓ {h}")
        lines.append("")

    return "\n".join(lines)


def print_review(output: ReviewOutput, as_json: bool = False, as_markdown: bool = False) -> None:
    if as_json:
        print(format_json(output))
        return

    if as_markdown:
        print(format_markdown(output))
        return

    style = _VERDICT_STYLE.get(output.verdict, "bold white")
    console.print(Panel(f"[{style}]{output.verdict.upper()}[/{style}]", title="Verdict"))
    console.print(f"\n[bold]Summary[/bold]\n{output.summary}\n")

    if output.issues:
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        table.add_column("Severity", style="bold", width=10)
        table.add_column("File", width=30)
        table.add_column("Issue")
        table.add_column("Suggestion")
        table.add_column("Evidence", style="dim")

        for issue in output.issues:
            sev_style = _SEVERITY_STYLE.get(issue.severity, "white")
            table.add_row(
                f"[{sev_style}]{issue.severity}[/{sev_style}]",
                issue.file,
                issue.description,
                issue.suggestion,
                issue.evidence,
            )
        console.print(table)

    if output.highlights:
        console.print("[bold green]Highlights[/bold green]")
        for h in output.highlights:
            console.print(f"  [green]✓[/green] {h}")
