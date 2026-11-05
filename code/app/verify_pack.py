"""Check that every pack file is unchanged, tracked by git and not ignored.

Run from code/:  python -m app verify-pack
Exit code 0 only when all three hold for every file listed in pack.json.
"""
import hashlib
import json
import subprocess
from pathlib import Path

from .config import REPO_DIR


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO_DIR), *args], capture_output=True, text=True)


def verify_pack() -> list[str]:
    pack = json.loads((REPO_DIR / "pack.json").read_text(encoding="utf-8"))
    files: dict[str, str] = pack["files"]
    problems: list[str] = []

    for rel, expected in files.items():
        path = REPO_DIR / rel
        if not path.is_file():
            problems.append(f"missing: {rel}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            problems.append(f"changed: {rel}")

    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        problems.append("not a git repository")
        return problems

    tracked = set(_git("ls-files").stdout.splitlines())
    ignored = set(_git("check-ignore", "--no-index", *files).stdout.splitlines())
    for rel, expected in files.items():
        if rel in ignored:
            problems.append(f"ignored by .gitignore: {rel}")
        if rel not in tracked:
            problems.append(f"not committed/staged: {rel}")
            continue
        # The bytes a fresh clone receives (index blob), not just the working copy.
        # Catches line-ending conversion (core.autocrlf) silently changing a pack file.
        blob = subprocess.run(["git", "-C", str(REPO_DIR), "show", f":{rel}"], capture_output=True).stdout
        if hashlib.sha256(blob).hexdigest() != expected:
            problems.append(f"git copy differs (line endings?): {rel}")
    return problems


def main() -> int:
    problems = verify_pack()
    total = len(json.loads((REPO_DIR / "pack.json").read_text(encoding="utf-8"))["files"])
    if problems:
        for p in problems:
            print(p)
        print(f"verify-pack: FAILED ({len(problems)} problems over {total} files)")
        return 1
    print(f"verify-pack: OK ({total} files unchanged, tracked and not ignored)")
    return 0
