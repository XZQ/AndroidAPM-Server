"""Scan candidate text files for accidentally committed plaintext credentials.

Both tracked files and untracked-but-not-ignored files are inspected, so a secret
about to be staged is caught before it enters the index. Git-ignored local
runtime material (`.local/`, `.env.*`) is never read or reported. Matches are
redacted in the output.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 2 * 1024 * 1024
BINARY_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".jar", ".woff", ".woff2"}
)

# Long random-looking API keys are flagged; short obvious test fixtures are not.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("query key", re.compile(r"\bapmq1_[A-Za-z0-9_-]{24,}")),
    ("ci key", re.compile(r"\bapmci1_[A-Za-z0-9_-]{24,}")),
    ("GitHub token", re.compile(r"\bghp_[A-Za-z0-9]{30,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)
# Only secret-valued settings are checked. Public key ids, key versions, JSON
# documents and empty values are configuration, not committed credentials.
ASSIGNED_SECRET = re.compile(
    r"^[ \t]*(?:APM_[A-Z0-9_]*(?:_KEY_B64|_KEY|_SECRET_B64|_SECRET)"
    r"|POSTGRES_PASSWORD)[ \t]*[:=][ \t]*[\"']?([^\s\"'#,{}]+)",
    re.IGNORECASE | re.MULTILINE,
)
PLACEHOLDER_MARKERS = (
    "replace-with",
    "change-me",
    "test-only",
    "example",
    "dummy",
    "your-",
    "<",
    "${",
    "{}",
    "none",
    "null",
    "os.environ",
)


def is_placeholder(value: str) -> bool:
    """Return True for template values that are not real secrets."""
    lowered = value.lower()
    # Compose uses ${VAR:-default}; a lone '$' means the value is a reference, not a secret.
    return (
        not lowered
        or lowered.startswith("$")
        or any(marker in lowered for marker in PLACEHOLDER_MARKERS)
    )


def candidate_files() -> list[Path]:
    """Return tracked files plus untracked, non-ignored files (pre-commit coverage)."""
    names: set[str] = set()
    for arguments in (
        ["git", "ls-files", "-z"],
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
    ):
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell, no untrusted input
            arguments, cwd=ROOT, capture_output=True, check=True
        )
        names.update(name for name in result.stdout.decode("utf-8").split("\0") if name)
    return [ROOT / name for name in sorted(names)]


def findings(text: str) -> list[str]:
    """Return redacted finding labels for one file's contents."""
    labels: list[str] = []
    for label, pattern in PATTERNS:
        if pattern.search(text):
            labels.append(label)
    for match in ASSIGNED_SECRET.finditer(text):
        if not is_placeholder(match.group(1)):
            labels.append("assigned secret value")
            break
    return labels


def main() -> int:
    """Return non-zero when any candidate file appears to contain a plaintext secret."""
    failures: list[str] = []
    for path in candidate_files():
        if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for label in findings(text):
            failures.append(f"{path.relative_to(ROOT).as_posix()}: {label}")
    if failures:
        for failure in failures:
            print(f"ERROR: possible plaintext credential in {failure}", file=sys.stderr)
        return 1
    print("Candidate-file secret scan passed (tracked + untracked, non-ignored).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
