"""Models a run may use for each of its roles.

A selection is `{"provider": ..., "model": ..., "effort": ...}` with provider
"claude", "aiprover", or a hosted provider (`HOSTED_PROVIDERS`).
For Claude, `model` is a model id, `effort` a reasoning level the model
accepts (empty = the model default) and `tools` whether the role has Lean
tools (agents/claude_cli.py); the orchestrator's `judge_tools` covers its
formalization and sketch judges. For a hosted provider, `model` is any
model id the provider serves and `effort` is empty. For AIProver, `model` is a version, whose
checkpoint the Vista model server must serve, and `effort` is empty: the
reasoning of the model is not configurable.

Roles (pipeline name in parentheses): the orchestrator (captain) formalizes,
sketches and repairs; the auditor judges the formalization against the
informal statement; the reviewer, an independent model, judges the finished
proof; the writer writes the LaTeX report; the subagent (solver) proves the
lemmas. A role absent from a stored selection keeps the served config's model.
"""

import json
import re
from pathlib import Path

from .vista import CHECKPOINTS, GPU_GROUPS

EFFORTS = ("low", "medium", "high", "xhigh", "max")
CLAUDE_MODELS = {
    "claude-opus-5-5": {"label": "Opus 5.5", "efforts": EFFORTS},
    "claude-sonnet-5-5": {"label": "Sonnet 5.5", "efforts": EFFORTS},
    "claude-haiku-4-5-20251001": {"label": "Haiku 4.5", "efforts": ()},
}
HOSTED_PROVIDERS = {
    "openai": "OpenAI",
    "huggingface": "Hugging Face",
    "openrouter": "OpenRouter",
}
MODEL_ID_RE = re.compile(r"[\w.:/-]+")
AIPROVER_VERSIONS = {
    "trained": "AIProver trained (FP8, Vista)",
    "base": "AIProver base (bf16, Vista)",
    "dgx": "AIProver on cic-dgx-01 (persistent)",
}

DEFAULT_SELECTION = {
    "orchestrator": {
        "provider": "claude",
        "model": "claude-opus-5-5",
        "effort": "high",
        "tools": True,
        "judge_tools": False,
    },
    "auditor": {
        "provider": "claude",
        "model": "claude-sonnet-5-5",
        "effort": "",
    },
    "reviewer": {
        "provider": "claude",
        "model": "claude-opus-5-5",
        "effort": "high",
    },
    "writer": {
        "provider": "claude",
        "model": "claude-opus-5-5",
        "effort": "high",
    },
    "subagent": {"provider": "aiprover", "model": "trained", "effort": ""},
    "handback": {
        "provider": "claude",
        "model": "claude-opus-5-5",
        "effort": "high",
        "tools": True,
    },
}
ROLES = {
    "orchestrator": "captain",
    "auditor": "auditor",
    "reviewer": "reviewer",
    "writer": "writer",
    "subagent": "solver",
}
# Roles served by a phase of the captain (Claude only), by the phase-label
# prefix they set the model of.
PHASE_ROLES = {"handback": "handback"}
SELECTABLE_ROLES = (*ROLES, *PHASE_ROLES)
ROLE_LABELS = {"handback": "Hand-back"}
ROLE_DESCRIPTIONS = {
    "orchestrator": "Formalizes, sketches the proof and repairs",
    "auditor": "Judges the formalization against the statement",
    "reviewer": "Independently judges the finished proof",
    "writer": "Writes the LaTeX report",
    "subagent": "Proves the lemmas",
    "handback": "Answers hand-backs: proves, splits, restates or retries stuck lemmas",
}

# Chat roles reach AIProver through the reasoning proxy, as the
# solver's harness does (configs/aiprover/vista_logged.toml).
AIPROVER_ENDPOINT = "http://127.0.0.1:18565/v1"
AIPROVER_SERVED_MODEL = "aiprover"
CHAT_REPLY_TOKENS = 24576
# Claude calls per run: the served config limits them for a Claude orchestrator
# and auditor; a Claude subagent makes one call per lemma attempt as well.
CLAUDE_SUBAGENT_CALL_LIMIT = 40
SUBAGENT_TIMEOUT = 5400


def catalog() -> dict:
    """What the page offers, for `GET /api/models`."""
    return {
        "claude": [
            {
                "model": model,
                "label": info["label"],
                "efforts": list(info["efforts"]),
            }
            for model, info in CLAUDE_MODELS.items()
        ],
        "aiprover": [
            {"model": version, "label": label}
            for version, label in AIPROVER_VERSIONS.items()
        ],
        "hosted": [
            {"provider": provider, "label": label}
            for provider, label in HOSTED_PROVIDERS.items()
        ],
        "roles": [
            {
                "role": role,
                "label": ROLE_LABELS.get(role, role.capitalize()),
                "description": ROLE_DESCRIPTIONS[role],
            }
            for role in SELECTABLE_ROLES
        ],
        "phase_roles": list(PHASE_ROLES),
        "default": DEFAULT_SELECTION,
    }


def validate(selection: dict) -> dict:
    """The normalized selection of both roles; ValueError names the problem."""
    normalized = {}
    for role in SELECTABLE_ROLES:
        choice = selection.get(role) or DEFAULT_SELECTION[role]
        provider, model, effort = (
            choice.get(key) or "" for key in ("provider", "model", "effort")
        )
        if role in PHASE_ROLES and provider != "claude":
            raise ValueError(f"{role}: a phase of the captain uses Claude")
        if provider == "claude":
            if model not in CLAUDE_MODELS:
                raise ValueError(f"{role}: unknown Claude model {model!r}")
            efforts = CLAUDE_MODELS[model]["efforts"]
            if effort and effort not in efforts:
                raise ValueError(
                    f"{role}: {CLAUDE_MODELS[model]['label']} has no "
                    f"reasoning level {effort!r}"
                )
        elif provider in HOSTED_PROVIDERS:
            if not MODEL_ID_RE.fullmatch(model):
                raise ValueError(f"{role}: invalid model id {model!r}")
            effort = ""
        elif provider == "aiprover":
            if model not in AIPROVER_VERSIONS:
                raise ValueError(f"{role}: unknown AIProver version {model!r}")
            effort = ""
        else:
            raise ValueError(f"{role}: unknown provider {provider!r}")
        normalized[role] = {
            "provider": provider,
            "model": model,
            "effort": effort,
        }
        if provider == "claude" and role != "subagent":
            normalized[role] |= tool_choice(role, choice)
    versions = checkpoint_names(normalized)
    if len(versions) > 1:
        raise ValueError(
            "roles that use AIProver must use the same version: "
            "the model server serves one checkpoint"
        )
    return normalized


def tool_choice(role: str, choice: dict) -> dict:
    """The Lean tools of a Claude role; an absent choice takes the role's
    default (on for the orchestrator except its judges, off otherwise)."""
    default = DEFAULT_SELECTION[role]
    tools = {"tools": bool(choice.get("tools", default.get("tools", False)))}
    if role == "orchestrator":
        tools["judge_tools"] = bool(
            choice.get("judge_tools", default["judge_tools"])
        )
    return tools


def checkpoint_names(selection: dict) -> set[str]:
    """AIProver versions the selection uses."""
    return {
        choice["model"]
        for choice in selection.values()
        if choice["provider"] == "aiprover"
    }


# AIProver versions served by a persistent server, each behind its own
# reasoning proxy and harness configuration; runs that use only these need no
# Vista server job.
PERSISTENT_VERSIONS = {"dgx"}
VERSION_ENDPOINTS = {"dgx": GPU_GROUPS["dgx"].endpoint}
HARNESS_CONFIGS = {"dgx": GPU_GROUPS["dgx"].solver_config}
# Session time limits (seconds) of versions slower than the Vista servers; the
# DGX completes a median 11 turns per hour against 76 on Vista.
SOLVER_TIMEOUTS = {"dgx": 10800}
# Sessions per run on the DGX: 32 concurrent requests, the load its owners
# asked to test.
VERSION_SETTINGS = {"dgx": {"aiprover_session_slots": 32}}


def checkpoint_of(selection: dict) -> str | None:
    """Path of the checkpoint the run needs a Vista server job to serve, if any."""
    names = checkpoint_names(selection) - PERSISTENT_VERSIONS
    return CHECKPOINTS[next(iter(names))] if names else None


def build_config(base_config: Path, selection: dict) -> dict:
    """The served config with the agents of `selection` (already validated)."""
    config = json.loads(base_config.read_text())
    agents = config["agents"]
    for role, choice in selection.items():
        if role in PHASE_ROLES:
            continue
        # The pipeline gives a reviewer or writer absent from the config the
        # captain's specification.
        spec = agents.get(ROLES[role]) or agents["captain"]
        if choice["provider"] == "claude":
            spec = {
                key: value
                for key, value in spec.items()
                if key in ("timeout", "max_output_tokens")
            }
            spec |= {"backend": "claude", "model": choice["model"]}
            if choice["effort"]:
                spec["effort"] = choice["effort"]
            if role != "subagent":
                tools = tool_choice(role, choice)
                spec["tools"] = tools["tools"]
                if "judge_tools" in tools:
                    spec["phases"] = {
                        "judge": {"tools": tools["judge_tools"]}
                    }
            if role == "subagent":
                spec["timeout"] = SUBAGENT_TIMEOUT
        elif choice["provider"] in HOSTED_PROVIDERS:
            spec = {
                "backend": "openai_compatible",
                "provider": choice["provider"],
                "model": choice["model"],
                "timeout": spec.get("timeout", 2400),
                "max_tokens": CHAT_REPLY_TOKENS,
            }
        elif role != "subagent":
            spec = {
                "backend": "openai_compatible",
                "model": AIPROVER_SERVED_MODEL,
                "base_url": VERSION_ENDPOINTS.get(
                    choice["model"], AIPROVER_ENDPOINT
                ),
                "timeout": spec.get("timeout", 2400),
                "max_tokens": CHAT_REPLY_TOKENS,
            }
        else:
            # The served config's solver is the AIProver harness; a persistent
            # version has its own harness configuration.
            if choice["model"] in HARNESS_CONFIGS:
                agents["solver"] = {
                    **agents["solver"],
                    "config": HARNESS_CONFIGS[choice["model"]],
                    "timeout": SOLVER_TIMEOUTS.get(
                        choice["model"], agents["solver"].get("timeout")
                    ),
                }
            continue
        agents[ROLES[role]] = spec
    captain = agents["captain"]
    for role, prefix in PHASE_ROLES.items():
        choice = selection.get(role)
        if choice and captain.get("backend") == "claude":
            # The phase keeps its other options (limits) from the run's
            # captain phases; an empty effort keeps the captain's.
            override = {"model": choice["model"], "tools": choice["tools"]}
            if choice["effort"]:
                override["effort"] = choice["effort"]
            phases = captain.setdefault("phases", {})
            phases[prefix] = {**phases.get(prefix, {}), **override}
    for version in checkpoint_names(selection):
        config |= VERSION_SETTINGS.get(version, {})
    if selection.get("subagent", {}).get("provider") == "claude":
        config["max_claude_calls"] = CLAUDE_SUBAGENT_CALL_LIMIT
    return config
