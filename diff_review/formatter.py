import json
import sys
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


def print_review(output: ReviewOutput, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(output.model_dump(), indent=2))
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

        for issue in output.issues:
            sev_style = _SEVERITY_STYLE.get(issue.severity, "white")
            table.add_row(
                f"[{sev_style}]{issue.severity}[/{sev_style}]",
                issue.file,
                issue.description,
                issue.suggestion,
            )
        console.print(table)

    if output.highlights:
        console.print("[bold green]Highlights[/bold green]")
        for h in output.highlights:
            console.print(f"  [green]✓[/green] {h}")
