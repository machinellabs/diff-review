import json
import re
from datetime import date
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box
from .schema import ReviewOutput
from .security import redact_output, redact_secrets

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
    return json.dumps(redact_output(output).model_dump(), indent=2)


def format_markdown(output: ReviewOutput, source: str = "") -> str:
    output = redact_output(output)
    verdict_display = output.verdict.upper().replace("_", " ")
    lines = [f"# diff-review: {verdict_display}\n"]

    meta = [f"**Date:** {date.today()}"]
    if source:
        meta.append(f"**Source:** {redact_secrets(source)}")
    lines.append("  \n".join(meta))
    lines.append("\n---\n")
    lines.append(f"## Summary\n\n{output.summary}\n")
    coverage = output.coverage
    status = "Complete" if coverage.complete else "Incomplete"
    lines.append(f"**Model coverage:** {status} — {len(coverage.reviewed_files)} files, {coverage.reviewed_chunks} chunks.\n")
    if coverage.skipped_files:
        lines.append("**Excluded files:** " + ", ".join(f"`{path}`" for path in coverage.skipped_files) + "\n")

    if output.issues:
        lines.append(f"---\n\n## Issues ({len(output.issues)} found)\n")
        for issue in output.issues:
            emoji = _SEVERITY_EMOJI.get(issue.severity, "⚪")
            rule_tag = f" [{issue.rule}]" if issue.rule else ""
            lines.append(f"### {emoji} {issue.severity.upper()}{rule_tag} — `{issue.file}`\n")
            lines.append(f"**Issue:** {issue.description}  ")
            lines.append(f"**Suggestion:** {issue.suggestion}  ")
            if issue.evidence:
                fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", issue.evidence)), default=0))
                lines.append(f"**Evidence:**\n{fence}\n{issue.evidence}\n{fence}")
            lines.append("")

    if output.highlights:
        lines.append("---\n\n## Highlights\n")
        for h in output.highlights:
            lines.append(f"- ✓ {h}")
        lines.append("")

    return "\n".join(lines)


def print_review(output: ReviewOutput, as_json: bool = False, as_markdown: bool = False) -> None:
    output = redact_output(output)
    if as_json:
        print(format_json(output))
        return

    if as_markdown:
        print(format_markdown(output))
        return

    style = _VERDICT_STYLE.get(output.verdict, "bold white")
    console.print(Panel(f"[{style}]{output.verdict.upper()}[/{style}]", title="Verdict"))
    console.print("\n[bold]Summary[/bold]")
    console.print(Text(output.summary + "\n"))
    coverage = output.coverage
    status = "complete" if coverage.complete else "incomplete"
    console.print(f"Model coverage: {status} — {len(coverage.reviewed_files)} files, {coverage.reviewed_chunks} chunks.")
    if coverage.skipped_files:
        console.print(Text("Excluded files: " + ", ".join(coverage.skipped_files)))

    if output.issues:
        has_rules = any(issue.rule for issue in output.issues)
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        table.add_column("Severity", style="bold", width=10)
        if has_rules:
            table.add_column("Rule", width=12)
        table.add_column("File", width=30)
        table.add_column("Issue")
        table.add_column("Suggestion")
        table.add_column("Evidence", style="dim")

        for issue in output.issues:
            sev_style = _SEVERITY_STYLE.get(issue.severity, "white")
            row = [f"[{sev_style}]{issue.severity}[/{sev_style}]"]
            if has_rules:
                row.append(Text(issue.rule))
            row += [Text(value) for value in (issue.file, issue.description, issue.suggestion, issue.evidence)]
            table.add_row(*row)
        console.print(table)

    if output.highlights:
        console.print("[bold green]Highlights[/bold green]")
        for h in output.highlights:
            console.print(Text(f"  ✓ {h}"))


_INPUT_COST_PER_TOKEN = 3.0 / 1_000_000   # $3 / MTok  (claude-sonnet-4-6)
_OUTPUT_COST_PER_TOKEN = 15.0 / 1_000_000  # $15 / MTok


def print_token_usage(usage: dict) -> None:
    if not usage:
        return
    r_in = usage.get("review_input_tokens", 0)
    r_out = usage.get("review_output_tokens", 0)
    s_in = usage.get("synthesis_input_tokens", 0)
    s_out = usage.get("synthesis_output_tokens", 0)
    t_in = r_in + s_in
    t_out = r_out + s_out
    cost = t_in * _INPUT_COST_PER_TOKEN + t_out * _OUTPUT_COST_PER_TOKEN
    console.print(
        f"\n[dim]Tokens — "
        f"reviews: {r_in:,} in / {r_out:,} out  "
        f"| synthesis: {s_in:,} in / {s_out:,} out  "
        f"| total: {t_in:,} in / {t_out:,} out  "
        f"(~${cost:.4f})[/dim]"
    )
