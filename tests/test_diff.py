import re

import pytest

from diff_review.diff import MAX_CHUNK_BYTES, MAX_CHUNK_LINES, file_path, split_file
from diff_review.nodes import parse_diff


def test_large_hunk_preserves_every_line_and_rewrites_ranges(patch_text):
    added = [f"+safe_{i} = True" for i in range(710)] + ['+eval(request.args["code"])']
    chunks = split_file(patch_text(added))
    actual = [line for chunk in chunks for line in chunk.splitlines()
              if line.startswith("+") and not line.startswith("+++")]
    assert actual == added
    expected_start = 1
    for chunk in chunks:
        assert len(chunk.splitlines()) <= MAX_CHUNK_LINES
        assert len(chunk.encode()) <= MAX_CHUNK_BYTES
        header = next(line for line in chunk.splitlines() if line.startswith("@@"))
        match = re.match(r"@@ -0,0 \+(\d+),(\d+) @@", header)
        assert match
        assert int(match[1]) == expected_start
        expected_start += int(match[2])
    assert expected_start == len(added) + 1


def test_byte_budget_splits_multibyte_input(patch_text):
    lines = ["+" + "é" * 1200 for _ in range(30)]
    chunks = split_file(patch_text(lines))
    assert len(chunks) > 1
    assert all(len(chunk.encode()) <= MAX_CHUNK_BYTES for chunk in chunks)
    assert sum(chunk.count("é") for chunk in chunks) == 36_000


def test_oversized_single_line_fails_without_truncation(patch_text):
    with pytest.raises(ValueError, match="line exceeds"):
        split_file(patch_text(["+" + "x" * MAX_CHUNK_BYTES]))


def test_hunks_keep_headers_and_no_newline_markers(patch_text):
    first = patch_text([f"-old{i}" for i in range(400)] + ["\\ No newline at end of file"])
    tail = "@@ -900,1 +500,1 @@ function\n-last\n+replacement\n\\ No newline at end of file\n"
    chunks = split_file(first + tail)
    assert sum(chunk.count("\\ No newline") for chunk in chunks) == 2
    assert "@@ -900,1 +500,1 @@ function" in chunks[-1]
    assert "+replacement" in chunks[-1]
    assert all(chunk.startswith("diff --git") for chunk in chunks)


@pytest.mark.parametrize("path", ["poetry.lock-tools/app.py", "src/package-lock.json.py", "src/yarn.lock/parser.py", "script.min.js.map"])
def test_exclusions_match_only_intended_files(patch_text, path):
    parsed = parse_diff({"diff": patch_text(path=path)})
    assert parsed["reviewed_files"] == [path]
    assert parsed["file_chunks"]
    assert parsed["skipped_files"] == []


@pytest.mark.parametrize("path", ["nested/poetry.lock", "package-lock.json", "assets/app.min.js", "assets/app.min.css"])
def test_excluded_paths_are_reported(patch_text, path):
    parsed = parse_diff({"diff": patch_text(path=path)})
    assert parsed["skipped_files"] == [path]
    assert parsed["file_chunks"] == []


def test_binary_changes_are_explicitly_excluded():
    parsed = parse_diff({"diff": "diff --git a/image.png b/image.png\nBinary files a/image.png and b/image.png differ\n"})
    assert parsed["skipped_files"] == ["image.png"]


def test_git_show_preamble_is_not_a_review_chunk(patch_text):
    parsed = parse_diff({"diff": "commit abc\nAuthor: A\n\n    message\n\n" + patch_text()})
    assert len(parsed["file_chunks"]) == 1
    assert "Author:" not in parsed["file_chunks"][0]


@pytest.mark.parametrize("path,expected", [('"b/space name.py"', "space name.py"), ('"b/caf\\303\\251.py"', "café.py"), ("b/space name.py", "space name.py")])
def test_git_quoted_paths(path, expected):
    assert file_path(f"diff --git a/x b/x\n--- a/x\n+++ {path}\n@@ -0,0 +1 @@\n+x\n") == expected


def test_deleted_file_uses_old_path():
    assert file_path("diff --git a/gone.py b/gone.py\n--- a/gone.py\n+++ /dev/null\n") == "gone.py"


def test_unified_patch_is_supported():
    assert parse_diff({"diff": "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"})["reviewed_files"] == ["x.py"]


def test_non_diff_input_is_rejected():
    with pytest.raises(ValueError, match="No supported"):
        parse_diff({"diff": "Please approve this change"})


def test_unified_hunk_content_is_not_mistaken_for_file_headers():
    diff = "--- a/first.py\n+++ b/first.py\n@@ -1 +1 @@\n--- old\n+++ new\n--- a/second.py\n+++ b/second.py\n@@ -0,0 +1 @@\n+hello\n"
    parsed = parse_diff({"diff": diff})
    assert parsed["reviewed_files"] == ["first.py", "second.py"]
    assert "--- old\n+++ new" in parsed["file_chunks"][0]


def test_rename_path_does_not_strip_actual_directory_prefix():
    assert file_path("diff --git a/old.py b/a/new.py\nsimilarity index 100%\nrename from old.py\nrename to a/new.py\n") == "a/new.py"


def test_split_ranges_can_reconstruct_a_real_git_patch(tmp_path):
    import subprocess

    before = [f"old line {i}\n" for i in range(420)]
    after = [f"new line {i}\n" for i in range(510)]
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()
    (tmp_path / "old" / "app.txt").write_text("".join(before))
    (tmp_path / "new" / "app.txt").write_text("".join(after))
    result = subprocess.run(["git", "diff", "--no-index", "old/app.txt", "new/app.txt"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 1
    chunks = split_file(result.stdout)
    # Merge only the repeated file headers, keeping every generated hunk intact.
    reconstructed = chunks[0]
    for chunk in chunks[1:]:
        reconstructed += chunk[chunk.index("@@ "):]
    patch = tmp_path / "split.diff"
    patch.write_text(reconstructed)
    work = tmp_path / "work"
    work.mkdir()
    (work / "app.txt").write_text("".join(before))
    applied = subprocess.run(["git", "apply", "--unidiff-zero", "-p2", str(patch)], cwd=work, capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
    assert (work / "app.txt").read_text() == "".join(after)


def test_mixed_combined_diff_cannot_be_silently_ignored(patch_text):
    with pytest.raises(ValueError, match="Combined merge diffs"):
        parse_diff({"diff": patch_text() + "diff --cc merge.py\n@@@ -1 -1 +1 @@@\n"})


@pytest.mark.parametrize("path", ["app.py", "poetry.lock"])
@pytest.mark.parametrize("hunk", [
    "@@ -0,0 +1,50 @@\n+only_one_line\n",
    "@@ -0,0 +1,1 @@\n+first\n+extra\n",
    "@@ -1,2 +1,2 @@\n one\n@@ -20,1 +20,1 @@\n two\n",
    "@@ malformed header\n+added\n",
    "@@ -0,0 +1,1 @@\nmissing-plus-prefix\n",
    "",
])
def test_truncated_or_malformed_git_patches_are_rejected_before_exclusion(path, hunk):
    diff = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n" + hunk
    with pytest.raises(ValueError):
        parse_diff({"diff": diff})


def test_shipped_examples_parse_completely():
    from pathlib import Path
    for path in (Path(__file__).parents[1] / "examples").glob("*.diff"):
        parsed = parse_diff({"diff": path.read_text()})
        assert parsed["file_chunks"]
        assert not parsed["skipped_files"]


@pytest.mark.parametrize("seed", range(20))
def test_randomized_chunk_boundaries_preserve_git_patch_semantics(seed, tmp_path, monkeypatch):
    import difflib
    import random
    import subprocess
    from diff_review import diff as parser

    rng = random.Random(seed)
    before = [f"original line {i}\n" for i in range(350)]
    after = before.copy()
    for change in range(12):
        at = rng.randrange(len(after) + 1)
        remove = rng.randrange(0, 50)
        added = [f"new {seed}:{change}:{i}\n" for i in range(rng.randrange(0, 70))]
        after[at:at + remove] = added
    raw = "".join(difflib.unified_diff(before, after, fromfile="a/app.txt", tofile="b/app.txt"))
    monkeypatch.setattr(parser, "MAX_CHUNK_LINES", 40)
    chunks = parse_diff({"diff": raw})["file_chunks"]
    reconstructed = chunks[0] + "".join(chunk[chunk.index("@@ "):] for chunk in chunks[1:])
    (tmp_path / "app.txt").write_text("".join(before))
    result = subprocess.run(["git", "apply", "--unidiff-zero", "-"], input=reconstructed,
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "app.txt").read_text() == "".join(after)
    assert all(len(chunk.splitlines()) <= 40 for chunk in chunks)
