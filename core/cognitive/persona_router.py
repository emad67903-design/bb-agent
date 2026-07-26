"""
Implements: Section 3 -- core/cognitive/persona_router.py.
Also implements: Section 2 Layer 3 ("PersonaRouter -- five system
prompts: xss_expert, recon_architect, chain_strategist, business_logic,
root_hacker"), Section 1.4 principle 1 ("One Local Model + PersonaRouter
-- Apache-2.0 model; five system-prompt personas"), Section 13's
rejected-item rationale ("Second local model | RAM + context
fragmentation. PersonaRouter solves specialisation.").
Blueprint: bb_agent_v6.6_final_blueprint.md

WEEK 2 SCOPE:

`PersonaRouter` is a pure selection component: given a persona name, it
returns that persona's system prompt (and the one, unchanging model
identifier every persona shares). It does NOT call Ollama, does NOT
import `core/governance/token_throttler.py`, and has no network or
subprocess access anywhere in this file -- verified by this module
having zero such imports (see test_persona_router.py's
`test_module_has_no_llm_calling_or_throttler_imports`). The components
that actually issue completions using a selected persona's system prompt
-- `core/cognitive/tool_selector.py` (~150 local calls/session, Section
2/3), and the Mental Model Builder's `role_mapper.py`/`flow_tracer.py`
(Section 3, already documented as using the `recon_architect` persona)
-- are not Week 2 deliverables; THEY will call
`token_throttler.record_local_call()` when they exist and actually make
a request, per docs/DECISIONS.md item 9's established call-site
deferral pattern. Wiring a call-counting side effect into a component
that makes no calls would be counting something that didn't happen.

SINGLE-MODEL INVARIANT (Section 1.4 principle 1 / Section 13's
rejected "second local model" item): every `Persona` below carries the
exact same `model` value -- there is no parameter, method, or code path
in this class that can make any persona resolve to a different model
string. `qwen2.5-coder:7b` (Apache 2.0) is asserted directly against
`core/governance/token_throttler.py`'s own docstring
("Records one local qwen2.5-coder:7b call") so this file cannot drift
from that value silently.

PersonaName / Persona placement (documented judgment call, same
pattern already used for `core/triggers/webhook_trigger.py`'s
WebhookEvent): these are NOT added to `core/ontology/enums.py`. Unlike
this week's TierLevel gap -- an ontology type referenced by name in two
separate Week 2 components with no declaration anywhere, which required
an explicit stop and authorization -- PersonaName has exactly one Week 2
consumer (this file) and is not referenced by name anywhere else in the
blueprint's own code blocks. Single file, no cross-component blast
radius: documented here and proceeded with, per the corrected standard
from this same session (docs/DECISIONS.md, Week 2 section).

System prompt WORDING below is this module's own authored content, not
a blueprint quotation -- the blueprint names the five personas and
documents exactly one of their uses (`recon_architect`, tied explicitly
to Mental Model Builder's HTML/JS parsing, Section 3/6.3); it gives no
literal prompt text for any of the five. Wording is freely revisable
later without breaking any contract, since nothing outside this file
parses or depends on the exact text -- only on the five names existing,
being swappable, and sharing one model.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Single-model invariant (see module docstring). Matches
# core/governance/token_throttler.py's own docstring for the local model
# exactly -- asserted in tests against that file's text, not just
# repeated here by hand.
_MODEL = "qwen2.5-coder:7b"


class PersonaName(str, Enum):
    """The five system-prompt personas, Section 2 Layer 3, spelled
    exactly as the blueprint names them."""

    XSS_EXPERT = "xss_expert"
    RECON_ARCHITECT = "recon_architect"
    CHAIN_STRATEGIST = "chain_strategist"
    BUSINESS_LOGIC = "business_logic"
    ROOT_HACKER = "root_hacker"


@dataclass(frozen=True)
class Persona:
    """One persona: a name and its system prompt. `model` is fixed to
    `_MODEL` for every instance -- see single-model invariant above.

    Attributes:
        name: One of the five PersonaName values.
        system_prompt: The system-prompt text a caller (ToolSelector,
            role_mapper.py, flow_tracer.py, etc.) supplies to the local
            model when acting under this persona.
        model: Always `_MODEL`. Not settable per-instance to anything
            else (dataclass field default, not a constructor override
            path a caller could point elsewhere).
    """

    name: PersonaName
    system_prompt: str
    model: str = _MODEL


def _build_personas() -> dict[PersonaName, Persona]:
    """Authored system-prompt content -- see module docstring."""
    return {
        PersonaName.XSS_EXPERT: Persona(
            PersonaName.XSS_EXPERT,
            "You are the XSS specialist within BB-Agent's Cognitive Kernel "
            "(Section 7.1). You reason about reflected, stored, and DOM-based "
            "cross-site scripting: where untrusted input reaches an HTML, "
            "attribute, JS, or URL sink, and which context-appropriate probe "
            "would prove it. You never propose anything beyond a benign "
            "console.log probe on a test account -- no cookie exfiltration, no "
            "session takeover payloads, no third-party callback beyond the "
            "agent's own instrumentation. Every endpoint you reason about must "
            "already be confirmed in scope; you do not speculate about hosts "
            "outside the current target.",
        ),
        PersonaName.RECON_ARCHITECT: Persona(
            PersonaName.RECON_ARCHITECT,
            "You are the reconnaissance and mental-model specialist within "
            "BB-Agent's Cognitive Kernel (Section 3, 6.3: Mental Model "
            "Builder). You parse raw HTML and JavaScript from fetched pages "
            "to identify roles, data flows, navigation structure, and "
            "candidate trust boundaries -- surfacing what a human analyst "
            "would notice on first read of a page's source, not guessing "
            "at application behavior you have not been shown. You do not "
            "issue requests yourself; you describe structure for "
            "Groq-tier synthesis to reason over next.",
        ),
        PersonaName.CHAIN_STRATEGIST: Persona(
            PersonaName.CHAIN_STRATEGIST,
            "You are the exploit-chaining specialist within BB-Agent's "
            "Cognitive Kernel, supporting ChainAgent (Section 2 Layer 6, "
            "Section 6.1's Chain Discovery phase). You reason about how "
            "already-CONFIRMED findings might link into a multi-hop attack "
            "path across the target's attack graph -- never about unconfirmed "
            "speculation standing in for a real finding. Every hop you "
            "propose must terminate in a safe, non-destructive proof step; "
            "you operate within the session's ChainBudget (max depth 4, "
            "max 10 nodes/chain, Section 8.4) and stop rather than exceed it.",
        ),
        PersonaName.BUSINESS_LOGIC: Persona(
            PersonaName.BUSINESS_LOGIC,
            "You are the business-logic specialist within BB-Agent's "
            "Cognitive Kernel, supporting BusinessLogicAgent (Section 7.6). "
            "You reason about workflow assumption violations -- state "
            "transitions a developer assumed were impossible (e.g. "
            "applying a coupon after removing the item it applied to) -- "
            "and how to demonstrate the resulting unexpected state safely, "
            "on a test account, without any real financial or irreversible "
            "consequence. You do not propose any step Section 10.1 marks "
            "human_required (financial transactions, irreversible state "
            "changes) even on a test account.",
        ),
        PersonaName.ROOT_HACKER: Persona(
            PersonaName.ROOT_HACKER,
            "You are the broad, adversarial-thinking specialist within "
            "BB-Agent's Cognitive Kernel -- used for open-ended hypothesis "
            "generation across vulnerability classes when no narrower "
            "specialist persona fits, and for creative but strictly bounded "
            "reasoning within the session's CreativityBudget (max 3 "
            "speculative chains/session, 5 calls each, auto-terminate on no "
            "signal in 2 steps -- Section 8.4). Thinking like an attacker "
            "means finding the most plausible weakness, not the most "
            "destructive one: every hypothesis you raise must still resolve "
            "to a Tier A/B/C action eligible for a safe, non-destructive "
            "proof (Section 10.1) -- Tier D destructive actions are never "
            "something you propose executing, only, at most, something you "
            "flag as requiring human judgment.",
        ),
    }


class PersonaRouter:
    """Section 2 Layer 3: single local model, five swappable system
    prompts. Pure selection -- see module docstring for what this class
    deliberately does not do (no LLM calls, no token_throttler wiring).
    """

    def __init__(self) -> None:
        self._personas: dict[PersonaName, Persona] = _build_personas()
        self._active: PersonaName = PersonaName.RECON_ARCHITECT  # Section 6.3: Phase 2 (Mental
        # Model Builder) is the first phase to use a persona at all, so
        # recon_architect is the natural default rather than an arbitrary
        # first-enum-member pick.

    def select(self, name: PersonaName) -> Persona:
        """Returns the Persona for `name`, without changing `active_persona`.

        Args:
            name: One of the five PersonaName values.

        Returns:
            The corresponding Persona (system_prompt + shared model).

        Raises:
            KeyError: If `name` is not a valid PersonaName member. (In
                practice unreachable through the PersonaName enum itself,
                since Python raises ValueError constructing an invalid
                enum member before this method could ever be called with
                one -- this only fires if a caller bypasses the enum,
                e.g. passes a raw string.)
        """
        return self._personas[name]

    @property
    def active_persona(self) -> Persona:
        return self._personas[self._active]

    def switch_to(self, name: PersonaName) -> Persona:
        """Changes the active persona and returns it (system-prompt swap,
        Section 1.4: "Persona = system-prompt swap. Never a second loaded
        model.")."""
        self._active = name
        return self._personas[self._active]

    def all_personas(self) -> tuple[Persona, ...]:
        """All five, in PersonaName declaration order -- e.g. for a
        caller that wants to enumerate/display them."""
        return tuple(self._personas[name] for name in PersonaName)
