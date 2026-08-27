"""Adapt Claude Agent SDK plugin folders into oc8 manifest tables.

Claude plugins may ship without ``plugin.toml``; oc8 extends them via an optional
overlay (``plugin.toml``, ``guardrails/``, ``setup/``, …) sitting beside the
standard Claude layout. See ``merge_claude_and_oc8`` for field precedence.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from oc8.capas.manifest import (
    ClaudeHookAction,
    ClaudeHookMatcher,
    ClaudeHooksSpec,
    ManifestError,
    SkillTemplateSpec,
    TemplateAgent,
    claude_hook_permissions,
)
from oc8.skills.importer import parse_skill

logger = logging.getLogger(__name__)

CLAUDE_MANIFEST_DIR = ".claude-plugin"
CLAUDE_MANIFEST_FILE = "plugin.json"
MCP_FILENAME = ".mcp.json"
HOOKS_DEFAULT = "hooks/hooks.json"
SKILLS_DIR = "skills"
AGENTS_DIR = "agents"
COMMANDS_DIR = "commands"
PLUGIN_ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}"

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)
_KEY = re.compile(r"^([A-Za-z][\w-]*):\s*(.*)$")

_UNSUPPORTED_COMPONENTS = frozenset(
    {"output-styles", "outputStyles", "themes", "monitors", "lspServers", "bin"}
)


def is_claude_plugin(folder: Path) -> bool:
    """True when ``folder`` looks like a Claude Agent SDK plugin root."""
    if (folder / CLAUDE_MANIFEST_DIR / CLAUDE_MANIFEST_FILE).is_file():
        return True
    if (folder / MCP_FILENAME).is_file():
        return True
    if (folder / HOOKS_DEFAULT).is_file():
        return True
    if (folder / SKILLS_DIR).is_dir() and any(
        (folder / SKILLS_DIR).glob("*/SKILL.md")
    ):
        return True
    if (folder / AGENTS_DIR).is_dir() and any((folder / AGENTS_DIR).glob("*.md")):
        return True
    if (folder / COMMANDS_DIR).is_dir() and any((folder / COMMANDS_DIR).glob("*.md")):
        return True
    return (folder / "SKILL.md").is_file()


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


def _parse_agent_md(text: str, *, path: str) -> TemplateAgent:
    match = _FRONTMATTER.match(text)
    if match is None:
        raise ManifestError(f"{path}: agent markdown requires YAML frontmatter")
    meta = _frontmatter(match.group(1))
    body = match.group(2).strip()
    name = str(meta.get("name") or Path(path).stem).strip()
    if not name:
        raise ManifestError(f"{path}: agent frontmatter missing name")
    max_turns = meta.get("maxTurns") or meta.get("max_turns") or 0
    try:
        max_steps = int(max_turns)
    except (TypeError, ValueError):
        max_steps = 0
    skills_raw = meta.get("skills") or []
    skills = [str(s) for s in skills_raw] if isinstance(skills_raw, list) else []
    description = str(meta.get("description") or "").strip()
    role_title = description.split(".")[0][:120] if description else name
    return TemplateAgent(
        name=name,
        role_title=role_title,
        mission=body,
        persona=description,
        skills=skills,
        max_steps=max_steps,
    )


def _resolve_paths(folder: Path, spec: str | list[str] | None, default: Path) -> list[Path]:
    if spec is None:
        return [default] if default.exists() else []
    entries = [spec] if isinstance(spec, str) else list(spec)
    out: list[Path] = []
    for entry in entries:
        p = (folder / entry).resolve()
        if not str(p).startswith(str(folder.resolve())):
            raise ManifestError(f"path {entry!r} escapes plugin root")
        out.append(p)
    return out


def _substitute_plugin_root(value: str, root: str) -> str:
    return value.replace(PLUGIN_ROOT_VAR, root)


def _substitute_mapping(data: Any, root: str) -> Any:
    if isinstance(data, str):
        return _substitute_plugin_root(data, root)
    if isinstance(data, list):
        return [_substitute_mapping(v, root) for v in data]
    if isinstance(data, dict):
        return {k: _substitute_mapping(v, root) for k, v in data.items()}
    return data


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"{path}: expected a JSON object")
    return raw


def _read_plugin_json(folder: Path) -> dict[str, Any]:
    path = folder / CLAUDE_MANIFEST_DIR / CLAUDE_MANIFEST_FILE
    if path.is_file():
        return _read_json(path)
    return {}


def skill_from_md(path: Path) -> SkillTemplateSpec:
    """Parse one Claude ``SKILL.md`` into oc8 skill fields."""
    return _skill_from_md(path)


def _skill_from_md(path: Path) -> SkillTemplateSpec:
    candidate = parse_skill(path.read_text(encoding="utf-8"), path=str(path), budget_tokens=0)
    if candidate is None:
        raise ManifestError(f"{path}: not a valid SKILL.md")
    return SkillTemplateSpec(
        name=candidate.name,
        description=candidate.description,
        instruction=candidate.instruction,
    )


def _collect_skills(folder: Path, manifest: dict[str, Any]) -> list[SkillTemplateSpec]:
    """Skills outside ``skills/`` (commands, root SKILL, custom manifest paths).

    The default ``skills/<slug>/SKILL.md`` tree is read by ``discovery._read_skills_folder``
    so hybrid capas can merge TOML and Markdown skills in one place.
    """
    specs: list[SkillTemplateSpec] = []
    seen: set[str] = set()

    def add(spec: SkillTemplateSpec) -> None:
        if spec.name in seen:
            raise ManifestError(f"duplicate skill name {spec.name!r}")
        seen.add(spec.name)
        specs.append(spec)

    root_skill = folder / "SKILL.md"
    if root_skill.is_file():
        add(_skill_from_md(root_skill))

    custom = manifest.get("skills")
    if custom is not None:
        for skills_root in _resolve_paths(folder, custom, folder / "__missing__"):
            if skills_root.is_file() and skills_root.name == "SKILL.md":
                add(_skill_from_md(skills_root))
                continue
            if not skills_root.is_dir():
                continue
            direct = skills_root / "SKILL.md"
            if direct.is_file():
                add(_skill_from_md(direct))
                continue
            for sub in sorted(skills_root.iterdir()):
                skill_md = sub / "SKILL.md"
                if sub.is_dir() and skill_md.is_file():
                    add(_skill_from_md(skill_md))

    for commands_root in _resolve_paths(folder, manifest.get("commands"), folder / COMMANDS_DIR):
        if commands_root.is_file() and commands_root.suffix == ".md":
            text = commands_root.read_text(encoding="utf-8")
            candidate = parse_skill(text, path=str(commands_root), budget_tokens=0)
            if candidate is None:
                body = text.strip()
                name = commands_root.stem.replace("_", " ").title()
                add(SkillTemplateSpec(name=name, instruction=body))
            else:
                add(
                    SkillTemplateSpec(
                        name=candidate.name,
                        description=candidate.description,
                        instruction=candidate.instruction,
                    )
                )
        elif commands_root.is_dir():
            for md in sorted(commands_root.glob("*.md")):
                text = md.read_text(encoding="utf-8")
                candidate = parse_skill(text, path=str(md), budget_tokens=0)
                if candidate is None:
                    add(SkillTemplateSpec(name=md.stem.replace("_", " ").title(), instruction=text.strip()))
                else:
                    add(
                        SkillTemplateSpec(
                            name=candidate.name,
                            description=candidate.description,
                            instruction=candidate.instruction,
                        )
                    )
    return specs


def _collect_agents(folder: Path, manifest: dict[str, Any]) -> list[TemplateAgent]:
    agents: list[TemplateAgent] = []
    for agents_root in _resolve_paths(folder, manifest.get("agents"), folder / AGENTS_DIR):
        if agents_root.is_file() and agents_root.suffix == ".md":
            agents.append(_parse_agent_md(agents_root.read_text(encoding="utf-8"), path=str(agents_root)))
        elif agents_root.is_dir():
            for md in sorted(agents_root.glob("*.md")):
                agents.append(_parse_agent_md(md.read_text(encoding="utf-8"), path=str(md)))
    return agents


def _load_mcp_payload(folder: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    inline = manifest.get("mcpServers")
    if isinstance(inline, dict):
        return inline
    paths = _resolve_paths(folder, inline, folder / MCP_FILENAME)
    merged: dict[str, Any] = {}
    for path in paths:
        if path.is_dir():
            continue
        data = _read_json(path) if path.suffix == ".json" else {}
        servers = data.get("mcpServers", data)
        if isinstance(servers, dict):
            merged.update(servers)
    if not merged and (folder / MCP_FILENAME).is_file():
        data = _read_json(folder / MCP_FILENAME)
        servers = data.get("mcpServers", data)
        if isinstance(servers, dict):
            merged.update(servers)
    return merged


def _collect_tool_pack(folder: Path, manifest: dict[str, Any]) -> dict[str, Any] | None:
    servers = _load_mcp_payload(folder, manifest)
    if not servers:
        return None
    root = str(folder.resolve())
    connections: list[dict[str, Any]] = []
    for key, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        command = str(cfg.get("command") or "")
        args = list(cfg.get("args") or [])
        env = dict(cfg.get("env") or {})
        substituted = _substitute_mapping({"command": command, "args": args, "env": env}, root)
        connections.append(
            {
                "key": key.replace("-", "_"),
                "name": key,
                "transport": cfg.get("transport", "stdio"),
                "server_url": cfg.get("url", ""),
                "scopes": cfg.get("scopes", []),
                "config": {
                    "command": substituted["command"],
                    "args": substituted["args"],
                    "env": substituted["env"],
                },
            }
        )
    if not connections:
        return None
    return {"connections": connections}


def _parse_hook_action(raw: dict[str, Any]) -> ClaudeHookAction:
    return ClaudeHookAction.model_validate(raw)


def _parse_hooks_file(path: Path) -> ClaudeHooksSpec:
    data = _read_json(path)
    events_raw = data.get("hooks", data)
    if not isinstance(events_raw, dict):
        raise ManifestError(f"{path}: hooks must be a JSON object")
    events: dict[str, list[ClaudeHookMatcher]] = {}
    for event_name, matchers in events_raw.items():
        if not isinstance(matchers, list):
            continue
        parsed: list[ClaudeHookMatcher] = []
        for entry in matchers:
            if not isinstance(entry, dict):
                continue
            hooks_raw = entry.get("hooks") or []
            actions = [_parse_hook_action(h) for h in hooks_raw if isinstance(h, dict)]
            parsed.append(
                ClaudeHookMatcher(
                    matcher=str(entry.get("matcher") or ""),
                    hooks=actions,
                )
            )
        if parsed:
            events[event_name] = parsed
    return ClaudeHooksSpec(events=events)


def _collect_hooks(folder: Path, manifest: dict[str, Any]) -> ClaudeHooksSpec | None:
    inline = manifest.get("hooks")
    spec: ClaudeHooksSpec | None = None
    if isinstance(inline, dict):
        spec = _parse_hooks_file_from_dict(inline)
    else:
        paths = _resolve_paths(folder, inline, folder / HOOKS_DEFAULT)
        for path in paths:
            if path.is_file():
                spec = _parse_hooks_file(path)
                break
    if spec is None or not spec.events:
        return None
    return spec


def _parse_hooks_file_from_dict(data: dict[str, Any]) -> ClaudeHooksSpec:
    events_raw = data.get("hooks", data)
    if not isinstance(events_raw, dict):
        return ClaudeHooksSpec()
    events: dict[str, list[ClaudeHookMatcher]] = {}
    for event_name, matchers in events_raw.items():
        if not isinstance(matchers, list):
            continue
        parsed: list[ClaudeHookMatcher] = []
        for entry in matchers:
            if not isinstance(entry, dict):
                continue
            hooks_raw = entry.get("hooks") or []
            actions = [_parse_hook_action(h) for h in hooks_raw if isinstance(h, dict)]
            parsed.append(
                ClaudeHookMatcher(
                    matcher=str(entry.get("matcher") or ""),
                    hooks=actions,
                )
            )
        if parsed:
            events[event_name] = parsed
    return ClaudeHooksSpec(events=events)


def _infer_type(
    *,
    agents: list[TemplateAgent],
    skills: list[SkillTemplateSpec],
    tool_pack: dict[str, Any] | None,
    explicit: str | None,
) -> str:
    if explicit:
        return explicit
    if agents:
        return "agent_template" if len(agents) == 1 else "department_template"
    if tool_pack:
        return "tool_pack"
    if len(skills) == 1:
        return "skill"
    if skills:
        return "skill"
    return "agent_template"


def _unsupported_warnings(manifest: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for key in _UNSUPPORTED_COMPONENTS:
        if key in manifest and manifest[key]:
            warnings.append(f"Claude component {key!r} is not supported in oc8 v1")
    return warnings


def adapt_claude_plugin(folder: Path, *, plugin_id: str) -> tuple[dict[str, Any], list[str]]:
    """Build an oc8 manifest table from Claude layout. Warnings are non-fatal."""
    manifest_json = _read_plugin_json(folder)
    name = str(manifest_json.get("name") or plugin_id).strip()
    if name != plugin_id:
        raise ManifestError(
            f"folder name {plugin_id!r} does not match Claude plugin name {name!r}"
        )

    warnings = _unsupported_warnings(manifest_json)
    skills = _collect_skills(folder, manifest_json)
    agents = _collect_agents(folder, manifest_json)
    tool_pack = _collect_tool_pack(folder, manifest_json)
    claude_hooks = _collect_hooks(folder, manifest_json)

    plugin_depends: list[str] = []
    for dep in manifest_json.get("dependencies") or []:
        if isinstance(dep, str):
            plugin_depends.append(dep)
        elif isinstance(dep, dict) and dep.get("name"):
            plugin_depends.append(str(dep["name"]))
            if dep.get("version"):
                warnings.append(
                    f"plugin dependency {dep['name']!r} semver constraint ignored in v1"
                )

    table: dict[str, Any] = {
        "name": name,
        "version": str(manifest_json.get("version") or "0.0.0"),
        "summary": str(manifest_json.get("description") or ""),
        "label": str(manifest_json.get("displayName") or manifest_json.get("display_name") or ""),
        "trust": "community",
        "source_format": "claude",
        "plugin_depends": plugin_depends,
    }

    inferred = _infer_type(agents=agents, skills=skills, tool_pack=tool_pack, explicit=None)
    table["type"] = inferred

    if len(agents) == 1:
        table["agent_template"] = agents[0].model_dump(mode="json")
    elif len(agents) > 1:
        table["department_template"] = {
            "agents": [a.model_dump(mode="json") for a in agents],
        }

    if len(skills) == 1 and inferred == "skill":
        table["skill_template"] = skills[0].model_dump(mode="json")
    elif skills:
        table["skill_pack"] = {"skills": [s.model_dump(mode="json") for s in skills]}

    if tool_pack:
        table["tool_pack"] = tool_pack

    if claude_hooks:
        table["claude_hooks"] = claude_hooks.model_dump(mode="json")

    perms = claude_hook_permissions(claude_hooks)
    if perms:
        table["permissions"] = perms

    return table, warnings


def _merge_skill_lists(
    left: list[dict[str, Any]], right: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    seen = {s["name"] for s in left}
    out = list(left)
    for spec in right:
        if spec["name"] in seen:
            raise ManifestError(f"duplicate skill name {spec['name']!r} in Claude/oc8 merge")
        seen.add(spec["name"])
        out.append(spec)
    return out


def merge_claude_and_oc8(
    claude_table: dict[str, Any],
    oc8_table: dict[str, Any],
) -> dict[str, Any]:
    """Merge Claude-derived and oc8-overlay tables.

    oc8 wins for governance (trust, permissions, sandbox, setup); components
    union (skills, agents, MCP, hooks).
    """
    merged = dict(claude_table)
    merged["source_format"] = "hybrid"

    for key in (
        "trust",
        "permissions",
        "capabilities",
        "sandbox",
        "setup",
        "config",
        "core_compat",
        "label",
        "summary",
        "icon",
        "entry_points",
        "handles",
        "requirements",
        "plugin_depends",
        "depends",
        "provides",
    ):
        if key in oc8_table and oc8_table[key]:
            merged[key] = oc8_table[key]

    if oc8_table.get("type"):
        merged["type"] = oc8_table["type"]

    for key in ("version", "name"):
        if oc8_table.get(key):
            merged[key] = oc8_table[key]

    # Skills union
    oc8_skills: list[dict[str, Any]] = []
    if oc8_table.get("skill_template"):
        oc8_skills.append(oc8_table["skill_template"])
    if oc8_table.get("skill_pack"):
        oc8_skills.extend(oc8_table["skill_pack"].get("skills") or [])

    claude_skills: list[dict[str, Any]] = []
    if claude_table.get("skill_template"):
        claude_skills.append(claude_table["skill_template"])
    if claude_table.get("skill_pack"):
        claude_skills.extend(claude_table["skill_pack"].get("skills") or [])

    all_skills = _merge_skill_lists(claude_skills, oc8_skills)
    merged.pop("skill_template", None)
    merged.pop("skill_pack", None)
    if len(all_skills) == 1 and merged.get("type") == "skill":
        merged["skill_template"] = all_skills[0]
    elif all_skills:
        merged["skill_pack"] = {"skills": all_skills}

    # Agent bodies: oc8 overlay replaces Claude when present
    if oc8_table.get("agent_template"):
        merged["agent_template"] = oc8_table["agent_template"]
    if oc8_table.get("department_template"):
        merged["department_template"] = oc8_table["department_template"]

    # Tool pack: union connections by key
    oc8_tp = oc8_table.get("tool_pack") or {}
    claude_tp = claude_table.get("tool_pack") or {}
    oc8_conns = {c.get("key", "default"): c for c in (oc8_tp.get("connections") or [])}
    for conn in claude_tp.get("connections") or []:
        key = conn.get("key", "default")
        if key not in oc8_conns:
            oc8_conns[key] = conn
    if oc8_conns:
        merged["tool_pack"] = {"connections": list(oc8_conns.values())}

    # Claude hooks: keep Claude; oc8 handles stay in handles
    if oc8_table.get("claude_hooks"):
        merged["claude_hooks"] = oc8_table["claude_hooks"]
    elif claude_table.get("claude_hooks"):
        merged["claude_hooks"] = claude_table["claude_hooks"]

    # Permissions: union
    perms = list(dict.fromkeys(list(merged.get("permissions") or []) + claude_hook_permissions(
        ClaudeHooksSpec.model_validate(merged["claude_hooks"]) if merged.get("claude_hooks") else None
    )))
    if perms:
        merged["permissions"] = perms

    return merged
