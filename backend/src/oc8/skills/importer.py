"""Bring a skill written for another agent into oc8 -- with a verdict attached.

Claude Code skills are a `SKILL.md` per directory: YAML frontmatter with a name
and a description, then prose. The envelope maps onto oc8's skill almost
one-to-one, which makes an importer tempting and a blind one a trap.

Two reasons it must judge rather than copy.

**Size.** Measured over the 1,074 SKILL.md on one developer machine, bodies run
to 104,000 characters -- roughly 25,000 tokens. Reported always; refused only
where somebody has said what the model can hold. oc8 delivers a skill as a TOOL
RESULT, so it does not merely cost one turn: it stays in the transcript for the
rest of the run. The runs that failed here were 189 and 698 tokens over the
model's window. It is not that the skill is too long; it is that the window is
small, and the same text is unremarkable behind a large model. So the verdict is
computed against the DEPARTMENT's own budget, never against a fixed number.

**Provenance.** A skill is an instruction an agent will follow. Importing one
from a public repository is importing instructions from a stranger -- the same
class of thing the `<external>` fence exists for, except that a skill is not
fenced, because oc8 itself is offering it. That is why this module produces a
PREVIEW that an operator confirms, and why the preview shows the text rather
than only its name.

What it cannot judge is whether the content makes sense here. Most public skills
are written for a coding agent with a shell and a filesystem; an oc8 agent has
neither. Those references are flagged, not resolved.
"""

from __future__ import annotations

import io
import re
import tarfile
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from oc8.knowledge.connectors.base import ConnectorError
from oc8.knowledge.connectors.fetcher import safe_fetch_bytes

#: Rough characters-per-token, matching the estimate the model router already
#: uses. Precision is not the point -- the difference between "fits" and "does
#: not" here is a factor, not a percent.
_CHARS_PER_TOKEN = 4

#: No budget at all. Where nobody has recorded what the model can hold, oc8 does
#: not know -- and inventing a number would be a judgement dressed as a fact.
#: Sizes are still SHOWN, so the operator can judge; nothing is refused for
#: being large until somebody says how large is too large.
NO_BUDGET = 0
DEFAULT_BUDGET_TOKENS = NO_BUDGET

#: Tools a Claude Code skill may name that no oc8 agent has. An agent here sits
#: on a network with no route off the host and acts only through its connections.
_ALIEN_TOOLS = {
    "bash", "read", "write", "edit", "glob", "grep", "notebookedit",
    "webfetch", "websearch", "task", "todowrite", "bashoutput", "killshell",
}

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)
#: Enough YAML for a frontmatter block: `key: value` and `- item`. A full parser
#: would be a dependency for four fields we actually read.
_KEY = re.compile(r"^([A-Za-z][\w-]*):\s*(.*)$")


@dataclass(frozen=True)
class Candidate:
    """One skill found at a source, with everything needed to decide on it."""

    name: str
    description: str
    instruction: str
    path: str
    tokens: int
    verdict: str  # "fits" | "tight" | "too_big"
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "instruction": self.instruction,
            "path": self.path,
            "tokens": self.tokens,
            "verdict": self.verdict,
            "warnings": list(self.warnings),
        }


def _frontmatter(block: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    key: str | None = None
    for raw in block.splitlines():
        if raw.strip().startswith("- ") and key:
            out.setdefault(key, [])
            if isinstance(out[key], list):
                out[key].append(raw.strip()[2:].strip().strip("\"'"))
            continue
        match = _KEY.match(raw)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value.startswith("[") and value.endswith("]"):
            out[key] = [v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()]
        elif value:
            out[key] = value.strip("\"'")
        else:
            out[key] = []
    return out


def parse_skill(text: str, *, path: str, budget_tokens: int) -> Candidate | None:
    """Read one SKILL.md. None when it is not one."""
    match = _FRONTMATTER.match(text)
    if match is None:
        return None
    meta = _frontmatter(match.group(1))
    body = match.group(2).strip()
    name = str(meta.get("name") or "").strip()
    if not name or not body:
        return None

    tokens = max(1, len(body) // _CHARS_PER_TOKEN)
    warnings: list[str] = []

    tools = meta.get("allowed-tools") or meta.get("allowed_tools") or []
    alien = sorted({str(t).strip().lower() for t in tools} & _ALIEN_TOOLS)
    if alien:
        warnings.append(
            "nennt Werkzeuge, die es hier nicht gibt: " + ", ".join(alien)
            + " — der Skill wurde für einen Agenten mit Shell und Dateisystem geschrieben"
        )
    if re.search(r"\b(references?|scripts?|assets)/[\w./-]+", body):
        warnings.append(
            "verweist auf mitgelieferte Dateien — ein oc8-Agent hat kein Dateisystem, "
            "diese Verweise laufen ins Leere"
        )
    if meta.get("hooks"):
        warnings.append("bringt Hooks mit, die oc8 nicht ausführt")

    if budget_tokens <= 0:
        # No ceiling recorded: report the size, judge nothing. A verdict here
        # would be oc8 guessing on the operator's behalf.
        verdict = "fits"
    elif tokens > budget_tokens:
        verdict = "too_big"
    elif tokens > budget_tokens // 2:
        verdict = "tight"
    else:
        verdict = "fits"

    return Candidate(
        name=name,
        description=str(meta.get("description") or "").strip(),
        instruction=body,
        path=path,
        tokens=tokens,
        verdict=verdict,
        warnings=warnings,
    )


def archive_url(source: str) -> str:
    """Where to fetch a source's archive.

    A forge-specific shortcut, kept deliberately small and in ONE place: a git
    URL is what an operator has, a tarball is what can be read without running
    git against a stranger's repository. Anything already pointing at an archive
    is passed through, so a forge nobody thought of is still usable.
    """
    source = source.strip().rstrip("/")
    if source.endswith((".tar.gz", ".tgz")):
        return source
    parsed = urlparse(source)
    path = parsed.path.removesuffix(".git").strip("/")
    if parsed.netloc in {"github.com", "www.github.com"} and path.count("/") == 1:
        return f"https://codeload.github.com/{path}/tar.gz/HEAD"
    raise ConnectorError(
        f"cannot work out an archive URL for {source!r} — give a git URL of the "
        "form https://github.com/owner/repo, or a direct .tar.gz link"
    )


def read_archive(blob: bytes, *, budget_tokens: int, limit: int = 200) -> list[Candidate]:
    """Every SKILL.md in an archive, judged.

    Members are read from the stream and never written to disk: a tar entry can
    name `../` and a path traversal during an import would be an odd way to lose
    a machine.
    """
    out: list[Candidate] = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile() or len(out) >= limit:
                continue
            if not member.name.lower().endswith("skill.md"):
                continue
            if member.size > 2_000_000:
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            text = handle.read().decode("utf-8", errors="replace")
            # Strip the archive's top directory, which carries a commit hash.
            path = member.name.split("/", 1)[-1]
            candidate = parse_skill(text, path=path, budget_tokens=budget_tokens)
            if candidate is not None:
                out.append(candidate)
    return sorted(out, key=lambda c: c.name.lower())


async def discover(source: str, *, budget_tokens: int) -> list[Candidate]:
    """Fetch a source and return what it offers, with a verdict on each."""
    blob = await safe_fetch_bytes(archive_url(source))
    try:
        return read_archive(blob, budget_tokens=budget_tokens)
    except tarfile.TarError as exc:
        raise ConnectorError(f"{source!r} is not a readable archive: {exc}") from exc
