"""Models a run may use for each of its roles.

A selection is `{"provider": ..., "model": ..., "effort": ...}` with provider
"claude", "codex", "aiprover", or "custom" (a server added on the page, which
`model` names). `tools` is whether a role other than the subagent has Lean
tools; the orchestrator's `judge_tools` covers its formalization and sketch
judges. They default to on for the orchestrator of a service and to off
otherwise, since a chat server may not support tool calls.
For Claude and Codex, `model` is a model id and `effort` a reasoning level the
model accepts (empty = the model default). For AIProver, `model` is a version, whose
checkpoint the Vista model server must serve, and `effort` is empty: the
reasoning of the model is not configurable.

Roles (pipeline name in parentheses): the orchestrator (orchestrator) formalizes,
sketches and repairs; the checker judges the formalization against the
informal statement; the reviewer, an independent model, judges the finished
proof; the writer writes the LaTeX report; the subagent (prover) proves the
lemmas; the escalation follows the orchestrator unless chosen. A role absent from
a stored selection keeps the served config's model.
"""

import json
import os
from pathlib import Path

from ..utils.vocabulary import modernize
from .model_servers import (
    CHECKPOINTS,
    GPU_GROUPS,
    custom_harness_config,
    custom_servers,
    key_file,
    server_endpoint,
)

EFFORTS = ("low", "medium", "high", "xhigh", "max")
CLAUDE_MODELS = {
    "claude-opus-5-5": {"label": "Opus 5.5", "efforts": EFFORTS},
    "claude-sonnet-5-5": {"label": "Sonnet 5.5", "efforts": EFFORTS},
    "claude-haiku-4-5-20251001": {"label": "Haiku 4.5", "efforts": ()},
}
CODEX_MODELS = {
    "gpt-6.1-sol": {"label": "GPT-6.1 Sol", "efforts": EFFORTS},
    "gpt-6-astra": {"label": "GPT-6 Astra", "efforts": EFFORTS},
    "gpt-6.1-terra": {"label": "GPT-6.1 Terra", "efforts": EFFORTS},
}
# Services with a sign-in, each with its models.
SERVICE_MODELS = {"claude": CLAUDE_MODELS, "codex": CODEX_MODELS}
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
    "checker": {
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
}
ROLES = {
    "orchestrator": "orchestrator",
    "checker": "checker",
    "reviewer": "reviewer",
    "writer": "writer",
    "subagent": "prover",
}
# Roles served by a phase of the orchestrator, by the phase-label prefix they set
# the model of.
PHASE_ROLES = {"escalation": "escalation"}
SELECTABLE_ROLES = (*ROLES, *PHASE_ROLES)
ROLE_LABELS = {"escalation": "Escalation"}
ROLE_DESCRIPTIONS = {
    "orchestrator": "Formalizes, sketches the proof and repairs",
    "checker": "Judges the formalization against the statement",
    "reviewer": "Independently judges the finished proof",
    "writer": "Writes the LaTeX report",
    "subagent": "Proves the lemmas",
    "escalation": "Answers escalations: proves, splits, restates or retries stuck lemmas",
}

# Chat roles reach AIProver through the reasoning proxy, as the
# prover's harness does (configs/aiprover/vista_logged.toml).
AIPROVER_ENDPOINT = "http://127.0.0.1:18565/v1"
AIPROVER_SERVED_MODEL = "aiprover"
CHAT_REPLY_TOKENS = 24576
# Hosted-model calls per run: the served config limits them for a hosted
# orchestrator and checker; a hosted subagent makes one call per lemma attempt
# as well.
CLAUDE_SUBAGENT_CALL_LIMIT = 40
SUBAGENT_TIMEOUT = 5400


def catalog() -> dict:
    """What the page offers, for `GET /api/models`."""
    return {
        **{
            service: [
                {
                    "model": model,
                    "label": info["label"],
                    "efforts": list(info["efforts"]),
                }
                for model, info in service_models.items()
            ]
            for service, service_models in SERVICE_MODELS.items()
        },
        "aiprover": [
            {"model": version, "label": label}
            for version, label in AIPROVER_VERSIONS.items()
        ],
        "servers": [
            {"name": name, "kind": server["kind"], "model": server["model"]}
            for name, server in custom_servers().items()
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
        choice = (
            selection.get(role)
            or (role in PHASE_ROLES and selection.get("orchestrator"))
            or DEFAULT_SELECTION.get(role)
            or DEFAULT_SELECTION["orchestrator"]
        )
        provider, model, effort = (
            choice.get(key) or "" for key in ("provider", "model", "effort")
        )
        if provider in SERVICE_MODELS:
            service_models = SERVICE_MODELS[provider]
            if model not in service_models:
                raise ValueError(f"{role}: unknown {provider} model {model!r}")
            if effort and effort not in service_models[model]["efforts"]:
                raise ValueError(
                    f"{role}: {service_models[model]['label']} has no "
                    f"reasoning level {effort!r}"
                )
        elif provider == "aiprover":
            if model not in AIPROVER_VERSIONS:
                raise ValueError(f"{role}: unknown AIProver version {model!r}")
            effort = ""
        elif provider == "custom":
            if model not in custom_servers():
                raise ValueError(f"{role}: unknown model server {model!r}")
            effort = ""
        else:
            raise ValueError(f"{role}: unknown provider {provider!r}")
        normalized[role] = {
            "provider": provider,
            "model": model,
            "effort": effort,
        }
        if role != "subagent":
            normalized[role] |= tool_choice(role, choice)
    versions = checkpoint_names(normalized)
    if len(versions) > 1:
        raise ValueError(
            "roles that use AIProver must use the same version: "
            "the model server serves one checkpoint"
        )
    return normalized


def tool_choice(role: str, choice: dict) -> dict:
    """The Lean tools of a role; an absent choice takes the default (on for a
    service's orchestrator or escalation, except the judges; off otherwise)."""
    default = role in ("orchestrator", *PHASE_ROLES) and (
        choice.get("provider") in SERVICE_MODELS
    )
    tools = {"tools": bool(choice.get("tools", default))}
    if role == "orchestrator":
        tools["judge_tools"] = bool(choice.get("judge_tools", False))
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
HARNESS_CONFIGS = {"dgx": GPU_GROUPS["dgx"].prover_config}
# Session time limits (seconds) of versions slower than the Vista servers; the
# DGX completes a median 11 turns per hour against 76 on Vista.
PROVER_TIMEOUTS = {"dgx": 10800}
# Sessions per run on the DGX. Its generation rate saturates near 60 tokens/s
# from about 16 running requests, and subagents raise requests to about 1.5
# per session; therefore 12 sessions keep the server near saturation while
# each session generates about twice as fast as at 32.
DGX_SLOTS = min(32, int(os.environ.get("AIPROVER_MAX_PARALLEL") or 12))
VERSION_SETTINGS = {"dgx": {"aiprover_session_slots": DGX_SLOTS}}


def checkpoint_of(selection: dict) -> str | None:
    """Path of the checkpoint the run needs a Vista server job to serve, if any."""
    names = checkpoint_names(selection) - PERSISTENT_VERSIONS
    return CHECKPOINTS[next(iter(names))] if names else None


def chat_spec(role: str, choice: dict, spec: dict) -> dict:
    """The agent of a role that a service or a chat server answers, keeping
    the options that `spec` gives it."""
    if choice["provider"] in SERVICE_MODELS:
        agent = {
            key: value
            for key, value in spec.items()
            if key in ("timeout", "max_output_tokens")
        }
        agent |= {"backend": choice["provider"], "model": choice["model"]}
        if choice["effort"]:
            agent["effort"] = choice["effort"]
    elif choice["provider"] == "custom":
        name, server = choice["model"], custom_servers()[choice["model"]]
        agent = {
            "backend": "openai_compatible",
            "model": server["model"],
            "base_url": server_endpoint(name, server),
            "api_key_file": str(key_file(name)),
            "timeout": spec.get("timeout", 2400),
            "max_tokens": CHAT_REPLY_TOKENS,
            "hosted": server["kind"] == "hosted",
        }
    else:
        agent = {
            "backend": "openai_compatible",
            "model": AIPROVER_SERVED_MODEL,
            "base_url": VERSION_ENDPOINTS.get(choice["model"], AIPROVER_ENDPOINT),
            "timeout": spec.get("timeout", 2400),
            "max_tokens": CHAT_REPLY_TOKENS,
        }
    if role == "subagent":
        return agent
    tools = tool_choice(role, choice)
    agent["tools"] = tools["tools"]
    if "judge_tools" in tools:
        agent["phases"] = {"judge": {"tools": tools["judge_tools"]}}
    return agent


def build_config(base_config: Path, selection: dict) -> dict:
    """The served config with the agents of `selection` (already validated)."""
    config = modernize(json.loads(base_config.read_text()))
    agents = config["agents"]
    for role, choice in selection.items():
        if role in PHASE_ROLES:
            continue
        # The pipeline gives a reviewer or writer absent from the config the
        # orchestrator's specification.
        spec = agents.get(ROLES[role]) or agents["orchestrator"]
        if (
            role == "subagent"
            and choice["provider"] == "custom"
            and custom_servers()[choice["model"]]["kind"] == "aiprover"
        ):
            name = choice["model"]
            agents["prover"] = {
                **agents["prover"],
                "config": custom_harness_config(name, custom_servers()[name]),
                "timeout": PROVER_TIMEOUTS["dgx"],
            }
            continue
        if role == "subagent" and choice["provider"] == "aiprover":
            # The served config's prover is the AIProver harness; a persistent
            # version has its own harness configuration.
            if choice["model"] in HARNESS_CONFIGS:
                agents["prover"] = {
                    **agents["prover"],
                    "config": HARNESS_CONFIGS[choice["model"]],
                    "timeout": PROVER_TIMEOUTS.get(
                        choice["model"], agents["prover"].get("timeout")
                    ),
                }
            continue
        spec = chat_spec(role, choice, spec)
        if role == "subagent" and choice["provider"] in SERVICE_MODELS:
            spec["timeout"] = SUBAGENT_TIMEOUT
        agents[ROLES[role]] = spec
    orchestrator = agents["orchestrator"]
    for role, prefix in PHASE_ROLES.items():
        choice = selection.get(role)
        if choice:
            # The phase keeps its other options (limits) from the run's
            # orchestrator phases; an empty effort keeps the orchestrator's. A phase on
            # another backend replaces the orchestrator's backend for its calls.
            override = chat_spec(role, choice, {})
            phases = orchestrator.setdefault("phases", {})
            phases[prefix] = {**phases.get(prefix, {}), **override}
    for version in checkpoint_names(selection):
        config |= VERSION_SETTINGS.get(version, {})
    if selection.get("subagent", {}).get("provider") in SERVICE_MODELS:
        config["max_hosted_calls"] = CLAUDE_SUBAGENT_CALL_LIMIT
    return config
