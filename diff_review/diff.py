"""Parse git/unified patches and split hunks without dropping changed lines."""

import ast
import re
from pathlib import PurePosixPath

MAX_CHUNK_LINES = 300
# A byte budget also bounds very long lines and non-ASCII input. Oversized
# individual lines fail explicitly rather than being partially reviewed.
MAX_CHUNK_BYTES = 24_000
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)")
_LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Gemfile.lock",
    "poetry.lock", "Cargo.lock",
}


def file_sections(diff: str) -> list[str]:
    lines = diff.splitlines(keepends=True)
    if any(line.startswith(("diff --cc ", "diff --combined ", "@@@")) for line in lines):
        raise ValueError("Combined merge diffs are unsupported; provide a two-parent comparison with git diff.")
    starts = [i for i, line in enumerate(lines) if line.startswith("diff --git ")]
    if not starts:
        old_left = new_left = 0
        for i, line in enumerate(lines):
            if old_left or new_left:
                old_left -= int(line.startswith((" ", "-")))
                new_left -= int(line.startswith((" ", "+")))
                if old_left < 0 or new_left < 0:
                    raise ValueError("Malformed unified diff hunk.")
                continue
            match = _HUNK.match(line)
            if match:
                old_left = int(match[2]) if match[2] is not None else 1
                new_left = int(match[4]) if match[4] is not None else 1
            elif line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
                starts.append(i)
        if old_left or new_left:
            raise ValueError("Incomplete unified diff hunk.")
    if not starts:
        raise ValueError("No supported git or unified diff found in the input.")
    starts.append(len(lines))
    return ["".join(lines[a:b]) for a, b in zip(starts, starts[1:])]


def _decode_path(value: str, strip_prefix: bool = True) -> str:
    value = value.split("\t", 1)[0].strip()
    if value.startswith('"'):
        try:
            value = ast.literal_eval(value)
            # Git's core.quotePath encodes UTF-8 bytes as octal escapes.
            value = value.encode("latin-1").decode("utf-8")
        except (ValueError, SyntaxError, UnicodeError):
            pass
    return value[2:] if strip_prefix and value.startswith(("a/", "b/")) else value


def file_path(section: str) -> str:
    old_path = None
    for line in section.splitlines():
        if line.startswith("@@"):
            break
        if line.startswith("--- "):
            old_path = _decode_path(line[4:])
        if line.startswith("+++ "):
            path = _decode_path(line[4:])
            return old_path if path == "/dev/null" and old_path else path
        if line.startswith("rename to "):
            return _decode_path(line[len("rename to "):], strip_prefix=False)
    first = section.splitlines()[0]
    # Metadata-only changes have no ---/+++ headers.
    match = re.match(r'^diff --git (?:".*?"|a/.*?) ("b/.*"|b/.*)$', first)
    if match:
        return _decode_path(match.group(1))
    raise ValueError("Cannot determine the changed file's path.")


def should_skip(section: str) -> bool:
    name = PurePosixPath(file_path(section)).name
    return (
        name in _LOCKFILES or name.endswith((".min.js", ".min.css"))
        or any(line.startswith(("Binary files ", "GIT binary patch"))
               for line in section.splitlines())
    )


def _fits(lines: list[str]) -> bool:
    return (len(lines) <= MAX_CHUNK_LINES
            and len("".join(lines).encode("utf-8")) <= MAX_CHUNK_BYTES)


def split_file(section: str) -> list[str]:
    """Keep small hunks intact; split large ones with accurate range headers."""
    lines = section.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if _HUNK.match(line)]
    if not starts:
        if not _fits(lines):
            raise ValueError(f"Metadata exceeds the review budget for {file_path(section)}.")
        return [section]
    header = lines[:starts[0]]
    starts.append(len(lines))
    chunks: list[str] = []
    current = header.copy()
    for start, end in zip(starts, starts[1:]):
        hunk = lines[start:end]
        if _fits(current + hunk):
            current.extend(hunk)
            continue
        if len(current) > len(header):
            chunks.append("".join(current))
            current = header.copy()
        if _fits(header + hunk):
            current.extend(hunk)
            continue

        match = _HUNK.match(hunk[0])
        assert match is not None
        old, new = int(match[1]), int(match[3])
        # Group the no-newline marker with its preceding line.
        units: list[list[str]] = []
        for line in hunk[1:]:
            if line.startswith("\\") and units:
                units[-1].append(line)
            elif line.startswith(("+", "-", " ")):
                units.append([line])
            else:
                raise ValueError(f"Unsupported hunk content in {file_path(section)}.")

        body: list[str] = []

        def segment(content: list[str]) -> list[str]:
            old_count = sum(line.startswith((" ", "-")) for line in content)
            new_count = sum(line.startswith((" ", "+")) for line in content)
            # Zero-length ranges name the preceding line, not the next one.
            old_start = old if old_count else max(0, old - 1)
            new_start = new if new_count else max(0, new - 1)
            return header + [
                f"@@ -{old_start},{old_count} +{new_start},{new_count} @@{match[5]}\n"
            ] + content

        # Original zero-length ranges also name the preceding line.
        if match[2] == "0":
            old += 1
        if match[4] == "0":
            new += 1
        for unit in units:
            if body and not _fits(segment(body + unit)):
                chunks.append("".join(segment(body)))
                old += sum(line.startswith((" ", "-")) for line in body)
                new += sum(line.startswith((" ", "+")) for line in body)
                body = []
            if not _fits(segment(body + unit)):
                raise ValueError(
                    f"A line exceeds the review budget for {file_path(section)}; "
                    "split the change before reviewing it."
                )
            body.extend(unit)
        if body:
            chunks.append("".join(segment(body)))
    if len(current) > len(header):
        chunks.append("".join(current))
    return chunks
