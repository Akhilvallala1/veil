"""Action firewall: every proposed computer action is checked before it runs.

Checks, in order (any can deny or escalate):
1. Leak-back: a `type` action containing a raw vault value means the model saw or
   guessed something it should not have. Deny.
2. Masked entities: tokens for `mask` entities (SSN, card, faces) can never be typed.
3. Flow policy: each token's value may only be typed into a destination app that a
   flow rule allows for that entity and origin. This holds even if the agent is
   fully hijacked by on-screen instructions.
4. Risk rules: sending/submitting/deleting/paying needs a human.
5. Meta safety checks: only codes listed in policy are auto-acknowledged, and only
   after 1 to 4 pass. Everything else goes to a human.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from veil.vault import TOKEN_RE, Vault

DEFAULT_POLICY = Path(__file__).resolve().parents[2] / "policy" / "default.yaml"


@dataclass
class Verdict:
    decision: str  # allow | deny | ask
    rule: str
    reason: str
    rewritten: dict | None = None  # action with tokens rehydrated (private side only)
    tokens: list[str] = field(default_factory=list)


class Policy:
    def __init__(self, path: Path | str = DEFAULT_POLICY) -> None:
        self.raw = yaml.safe_load(Path(path).read_text())
        self.entities: dict[str, str] = self.raw.get("entities", {})
        self.apps: dict[str, list[str]] = self.raw.get("apps", {})
        self.flows: list[dict] = self.raw.get("flows", [])
        self.approval: list[str] = [s.lower() for s in self.raw.get("approval_required", [])]
        self.auto_ack: set[str] = set(self.raw.get("auto_acknowledge_safety_checks", []))

    def app_for(self, url: str) -> str:
        """Most specific app whose URL prefix matches."""
        u = re.sub(r"^https?://", "", url or "")
        best, best_len = "unknown", -1
        for app, prefixes in self.apps.items():
            for p in prefixes:
                if u.startswith(p) and len(p) > best_len:
                    best, best_len = app, len(p)
        return best

    def flow_allowed(self, entity: str, origins: set[str], dest_app: str) -> bool:
        for rule in self.flows:
            if dest_app in rule.get("to", []) and entity in rule.get("entities", []) \
                    and (not origins or origins & set(rule.get("from", []))):
                return True
        return False


def _get(a: Any, k: str, default=None):
    return a.get(k, default) if isinstance(a, dict) else getattr(a, k, default)


def check_action(action: dict, vault: Vault, policy: Policy, dest_url: str,
                 focus_hint: str = "") -> Verdict:
    t = _get(action, "type")
    dest_app = policy.app_for(dest_url)
    if t == "type":
        text = _get(action, "text", "") or ""
        raw = vault.find_raw(TOKEN_RE.sub("", text))
        if raw:
            return Verdict("deny", "leak_back",
                           f"typed text contains raw sensitive value(s) {[e.token for e in raw]}")
        tokens = [m.group(1) or m.group(2) for m in TOKEN_RE.finditer(text)]
        for tok in tokens:
            e = vault.lookup(tok)
            if e is None:
                return Verdict("deny", "unknown_token", f"{tok} is not a known token")
            if policy.entities.get(e.entity, "token") == "mask":
                return Verdict("deny", "masked_entity",
                               f"{tok} is a {e.entity}; policy never allows typing it")
            if not policy.flow_allowed(e.entity, e.origins, dest_app):
                return Verdict("deny", "flow_policy",
                               f"{e.entity} ({tok}) from {sorted(e.origins) or ['unknown']} "
                               f"may not flow to {dest_app}")
        rewritten = dict(action) if isinstance(action, dict) else action.model_dump()
        rewritten["text"] = vault.rehydrate(text)[0]
        lowered = text.lower()
        for word in policy.approval:
            if word in lowered:
                return Verdict("ask", "approval_required", f"text mentions '{word}'", rewritten,
                               tokens)
        return Verdict("allow", "flow_policy" if tokens else "plain_text",
                       f"{len(tokens)} token(s) rehydrated for {dest_app}", rewritten, tokens)
    if t == "keypress":
        keys = [str(k).lower() for k in (_get(action, "keys") or [])]
        if focus_hint and any(k in ("enter", "return", "space", " ") for k in keys):
            for word in policy.approval:
                # Word match, not substring: "pay" must not fire on "Payer".
                if re.search(rf"\b{re.escape(word)}\b", focus_hint.lower()):
                    return Verdict("ask", "approval_required",
                                   f"Enter/Space while a '{word.title()}' control is on screen")
    if t in ("click", "double_click") and focus_hint:
        for word in policy.approval:
            if re.search(rf"\b{re.escape(word)}\b", focus_hint.lower()):
                return Verdict("ask", "approval_required", f"click on '{word.title()}'")
    return Verdict("allow", "default", t or "unknown")


def check_safety_checks(checks: list[dict], policy: Policy) -> tuple[list[dict], list[dict]]:
    """Split Meta's pending safety checks into (auto-acknowledgeable, needs-human)."""
    auto, human = [], []
    for c in checks or []:
        (auto if _get(c, "code") in policy.auto_ack else human).append(c)
    return auto, human
