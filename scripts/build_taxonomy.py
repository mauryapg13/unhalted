"""Generate the diagnosis taxonomy's factual base from Razorpay's documentation.

What this produces is *facts*, pinned to a commit of `razorpay/markdown-docs`:
which error reasons exist, per payment method, and how many distinct root causes
Razorpay documents for each. It does not decide what any of them mean — that
judgement lives in `core/taxonomy.py` and is ours to defend.

The separation matters. A reason's cause count is checkable by anyone against
Razorpay's published reference. A mapping from reason to recovery class is an
opinion. Mixing them would let the opinion borrow the authority of the fact.

    uv run python scripts/build_taxonomy.py            # write the data file
    uv run python scripts/build_taxonomy.py --check     # fail if it is stale

Their documentation is prose, not a schema. Every assumption this parser makes
is asserted below, and a shape change fails the run loudly rather than quietly
producing a thinner table.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.request
from datetime import UTC, datetime
from typing import Any

REPO = "razorpay/markdown-docs"
DOC_PATH = "errors/payments"
OUT = pathlib.Path(__file__).parent.parent / "src" / "unhalted" / "core" / "taxonomy_data.json"

#: Per-method top-error references, which carry root-cause detail.
METHOD_DOCS = {"card": "cards.md", "upi": "upi.md"}
#: The comprehensive list, which has no method attribution.
LIST_DOC = "list.md"

#: Razorpay documents recurring failures separately from one-off payments, and
#: a mandate debit failing after registration is the case this product exists
#: for. Reading only `errors/payments` missed it entirely. The section matters:
#: the same file also lists *registration* failures, which are a different
#: event — the customer never got a mandate at all.
RECURRING_DOCS = {
    "emandate": {
        "path": "payments/recurring-payments/emandate",
        "file": "errors.md",
        "section": "Subsequent Payments",
    },
}
#: Below this and the section parse is not being trusted.
MIN_REASONS_IN_SECTION = 12

HEADING = re.compile(r"^### (.+?)[ \t]*$", re.MULTILINE)
SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
DESCRIPTION = re.compile(r"\*\*Description\*\*:?\s*(.*)")

#: Anything below these and the parse is not being trusted.
MIN_REASONS_PER_METHOD = 8
MIN_REASONS_IN_LIST = 30


class ParseError(RuntimeError):
    """Their documentation changed shape. Fail rather than emit a thin table."""


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read().decode()


def docs_commit() -> dict[str, str]:
    """Pin to the commit that last touched the error references."""
    api = f"https://api.github.com/repos/{REPO}/commits?path={DOC_PATH}&per_page=1"
    commits = json.loads(fetch(api))
    if not commits:
        raise ParseError(f"no commits found for {REPO}/{DOC_PATH}")
    c = commits[0]
    return {"sha": c["sha"], "committed_at": c["commit"]["committer"]["date"]}


def raw_url(sha: str, filename: str, path: str = DOC_PATH) -> str:
    return f"https://raw.githubusercontent.com/{REPO}/{sha}/{path}/{filename}"


def parse_section_table(text: str, section: str, source: str) -> dict[str, dict[str, Any]]:
    """A `reason | explanation | next steps` table under one `## ` heading.

    Scoped to the section because the file carries several, and a registration
    failure is not a debit failure. Taking the whole file would mix them.
    """
    marker = re.search(rf"^#+\s*{re.escape(section)}\s*$", text, re.MULTILINE)
    if marker is None:
        raise ParseError(f"{source}: no '{section}' heading; the document shape changed")

    rest = text[marker.end():]
    following = re.search(r"^#{1,3} ", rest, re.MULTILINE)
    block = rest[: following.start()] if following else rest

    reasons: dict[str, dict[str, Any]] = {}
    for line in block.splitlines():
        if "|" not in line:
            continue
        head = line.split("|", 1)[0].strip().strip("`")
        if not SNAKE.match(head):
            continue
        parts = [p.strip() for p in line.split("|")]
        reasons[head] = {
            # Their recurring tables give one explanation per reason. Where a
            # reason is genuinely ambiguous they say so in prose, and that is
            # a judgement for core/taxonomy.py, not a count to invent here.
            "causes": 1,
            "cause_names": [],
            "description": (parts[1] if len(parts) > 1 else "")[:400],
            "source": f"{source}#{section}",
        }

    if len(reasons) < MIN_REASONS_IN_SECTION:
        raise ParseError(
            f"{source}#{section}: parsed only {len(reasons)} reasons, expected at "
            f"least {MIN_REASONS_IN_SECTION}. The document shape probably changed."
        )
    return reasons


def parse_method_doc(text: str, filename: str) -> dict[str, dict[str, Any]]:
    """Extract reasons and their documented root causes from a per-method doc.

    Structure, established by reading the source: a `### snake_case` heading
    opens a reason. A `### Title Case` heading that follows it names an
    additional root cause for that same reason. Some causes are named by an
    indented line rather than a heading, so causes are counted by the number of
    `**Description**` bullets in the block, which covers both forms.
    """
    marks = [(m.start(), m.group(1).strip()) for m in HEADING.finditer(text)]
    if not marks:
        raise ParseError(f"{filename}: no '### ' headings; the document shape changed")
    marks.append((len(text), "\x00end"))

    reasons: dict[str, dict[str, Any]] = {}
    current: str | None = None
    start = 0

    for pos, title in marks:
        is_reason = bool(SNAKE.match(title)) or title == "\x00end"
        if not is_reason:
            continue
        if current is not None:
            reasons[current] = _describe(text[start:pos], current, filename)
        current, start = title, pos

    if len(reasons) < MIN_REASONS_PER_METHOD:
        raise ParseError(
            f"{filename}: parsed only {len(reasons)} reasons, expected at least "
            f"{MIN_REASONS_PER_METHOD}. The document shape probably changed."
        )
    return reasons


def _describe(block: str, reason: str, filename: str) -> dict[str, Any]:
    descriptions = DESCRIPTION.findall(block)
    if not descriptions:
        raise ParseError(f"{filename}: '{reason}' has no **Description** bullet")

    # A cause is named by the last non-empty, non-bullet line before its
    # description — either a '### Title Case' heading or an indented label.
    names: list[str] = []
    lines = block.splitlines()
    for i, line in enumerate(lines):
        if "**Description**" not in line:
            continue
        for prev in reversed(lines[:i]):
            candidate = prev.strip().lstrip("#").strip()
            if not candidate or candidate.startswith("-"):
                continue
            if SNAKE.match(candidate):
                break
            names.append(candidate)
            break

    return {
        "causes": len(descriptions),
        "cause_names": names,
        "description": descriptions[0].strip()[:400],
        "source": filename,
    }


def parse_list_doc(text: str) -> dict[str, dict[str, Any]]:
    """The comprehensive reference: `reason | description | next steps` rows."""
    reasons: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        if "|" not in line:
            continue
        head = line.split("|", 1)[0].strip()
        if not SNAKE.match(head):
            continue
        parts = [p.strip() for p in line.split("|")]
        reasons[head] = {
            "causes": 1,
            "cause_names": [],
            "description": (parts[1] if len(parts) > 1 else "")[:400],
            "source": LIST_DOC,
        }

    if len(reasons) < MIN_REASONS_IN_LIST:
        raise ParseError(
            f"{LIST_DOC}: parsed only {len(reasons)} reasons, expected at least "
            f"{MIN_REASONS_IN_LIST}. The document shape probably changed."
        )
    return reasons


def build() -> dict[str, Any]:
    commit = docs_commit()
    sha = commit["sha"]

    by_method: dict[str, dict[str, Any]] = {}
    for method, filename in METHOD_DOCS.items():
        by_method[method] = parse_method_doc(fetch(raw_url(sha, filename)), filename)

    by_method["any"] = parse_list_doc(fetch(raw_url(sha, LIST_DOC)))

    for method, spec in RECURRING_DOCS.items():
        by_method[method] = parse_section_table(
            fetch(raw_url(sha, spec["file"], spec["path"])),
            spec["section"],
            f"{spec['path']}/{spec['file']}",
        )

    ambiguous = {
        f"{method}:{reason}": data["causes"]
        for method, reasons in by_method.items()
        for reason, data in reasons.items()
        if data["causes"] > 1
    }

    return {
        "generated_from": {
            "repo": REPO,
            "path": DOC_PATH,
            "commit": sha,
            "committed_at": commit["committed_at"],
        },
        "generated_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "summary": {
            "reasons_by_method": {m: len(r) for m, r in by_method.items()},
            "ambiguous": ambiguous,
        },
        "reasons": by_method,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check", action="store_true", help="exit non-zero if the committed data is stale"
    )
    args = ap.parse_args()

    try:
        data = build()
    except ParseError as e:
        print(f"taxonomy build failed: {e}", file=sys.stderr)
        return 2

    rendered = json.dumps(data, indent=2, sort_keys=True) + "\n"

    if args.check:
        if not OUT.exists():
            print(f"{OUT} does not exist", file=sys.stderr)
            return 1
        existing = json.loads(OUT.read_text())
        fresh = json.loads(rendered)
        for key in ("generated_at",):
            existing.pop(key, None)
            fresh.pop(key, None)
        if existing != fresh:
            print(
                "Razorpay's error documentation has changed since this was generated.",
                file=sys.stderr,
            )
            print("Regenerate with: uv run python scripts/build_taxonomy.py", file=sys.stderr)
            return 1
        print("taxonomy data is current")
        return 0

    OUT.write_text(rendered)
    s = data["summary"]
    print(f"pinned to {REPO}@{data['generated_from']['commit'][:12]}")
    for method, n in s["reasons_by_method"].items():
        print(f"  {method:<6} {n} reasons")
    print(f"  ambiguous ({len(s['ambiguous'])}):")
    for k, n in sorted(s["ambiguous"].items()):
        print(f"    {k:<34} {n} documented causes")
    print(f"written to {OUT.relative_to(pathlib.Path.cwd())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
