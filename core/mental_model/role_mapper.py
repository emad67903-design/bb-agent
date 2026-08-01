"""
Implements: Section 3 -- core/mental_model/role_mapper.py ("Local 7B,
recon_architect persona").
Design: mental_model_builder_prompt_design.md Sections 1-2.
Blueprint: bb_agent_v6.6_final_blueprint.md

Does NOT make its own Ollama call. Reads the `role_signals` field off
the same per-page `PageSignals` `flow_tracer.py` already computed --
one combined local-7B call per page, two readers (`flow_tracer.py`
owns the call and reads `flow_signals`; this module reads
`role_signals` off the identical response object). See
`flow_tracer.py`'s own module docstring and docs/DECISIONS.md item 39
for why: this mirrors the `network_observer.py`/`browser_tool.py`
precedent (one implementation, two call sites) already established in
this repository, applied again rather than invented fresh.
"""

from __future__ import annotations

from core.ontology.mental_model import PageSignals, RoleSignal


def extract_roles(page_signals: PageSignals) -> list[RoleSignal]:
    """Returns the role evidence already extracted for one page.

    Args:
        page_signals: A `PageSignals` produced by
            `flow_tracer.trace_page` for one fetched page.

    Returns:
        `page_signals.role_signals`, unchanged -- this function exists
        as the named, documented read path Section 3 assigns to
        `role_mapper.py`, not to transform the data.
    """
    return page_signals.role_signals
