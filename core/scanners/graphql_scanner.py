"""
Implements: Section 7.23 -- graphql_scanner.py
Blueprint: bb_agent_v6.6_final_blueprint.md

THREE TECHNIQUES, MATCHING SECTION 7.23'S TEXT EXACTLY: "Introspection
enabled, IDOR via ID queries, batching abuse." Each is its own
independent probe -- 0 to 3 candidates possible per `scan()` call, the
same "emit signals honestly, let downstream aggregation handle
thresholds" shape `cors_scanner.py` (item 91) already established for
its own four signals.

INTROSPECTION: A SINGLE, RICH QUERY DOES DOUBLE DUTY -- both the
introspection-enabled check AND schema discovery for the IDOR check
reuse the SAME response (one POST, not two), asking for `queryType`,
`types`, `fields`, and each field's `args` up front rather than a
minimal probe query first and a second, richer one only if needed --
simpler control flow, and Fast Lane doesn't need to economize a single
extra field in one already-cheap request the way it needs to economize
whole extra HTTP round-trips.

IDOR VIA ID QUERIES -- SCHEMA-DISCOVERED, NOT GUESSED, AND
SELF-CONTAINED THE SAME WAY `jwt_scanner.py` (item 95) IS: real GraphQL
IDOR testing typically compares two different ACCOUNTS' access to the
same object -- this project has no test-account/session infrastructure
yet (the same gap flagged for the "Batch 7" scanners, and already
worked around once for `jwt_scanner.py`). This technique instead uses
the introspected schema to find any query-type field with an argument
literally named `id` (case-insensitive) -- a common, if not universal,
convention -- and queries it with two different ID values under
whatever session `self.session` already carries, selecting only the
universal `__typename` meta-field (every GraphQL type supports it, so
no knowledge of the field's actual return type is needed). A signal
fires if EITHER query returns real object data rather than an
authorization error -- a weaker, single-session approximation of true
cross-account IDOR testing, flagged here rather than oversold as the
real thing, the same honesty `jwt_scanner.py`'s own "REPLAY TARGET"
limitation already models.

BATCHING ABUSE: `__typename` AGAIN, NOT A DESTRUCTIVE PROBE -- sends a
JSON-array batch of `{"query": "{__typename}"}` requests (Section
10.1's `TIER_C_RULES` spirit: safe, read-only, non-destructive), and
checks whether the server processed the FULL batch rather than
rejecting or truncating it. A modest, authored batch size (not
enumerated in Section 7.23's text) -- large enough to distinguish
"no limit enforced" from "happens to allow small batches," small enough
to stay clearly non-abusive.

`ExploitCandidate.parameter` DIFFERS BY TECHNIQUE WITHIN THIS ONE
SCANNER -- A NINTH EXTENSION TO ITEM 69'S LIST, BUT ONLY FOR TWO OF
THREE SIGNALS: introspection and batching are whole-body techniques
with no distinguishable parameter (`None`, the same shape XXE/
`api_versioning.py`/`jwt_scanner.py`/`hardcoded_credentials.py` already
have). The IDOR-via-ID signal DOES have a genuine, nameable thing being
tested -- the discovered field name (e.g. `"user"`) -- the same
"newly-discovered/added, but still a real name" reasoning
`prototype_pollution.py`/`mass_assignment.py` already established, not
`None`.

`interactsh_client` -- NOT DECLARED: Section 7.23 names no OOB
technique.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.ontology.findings import ExploitCandidate
from core.scanners.base_scanner import BaseScanner
from core.scanners.registry import register

PAYLOAD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "payloads" / "graphql_payloads.json"
RAW_RESPONSE_SNAPSHOT_CHARS = 512  # Section 10.7's call_target() convention

INTROSPECTION_QUERY = """
{
  __schema {
    queryType { name }
    types {
      name
      fields { name args { name } }
    }
  }
}
""".strip()


def _load_payloads(path: Path = PAYLOAD_FILE) -> list[dict]:
    """Loads and returns the `payloads` array from `graphql_payloads.json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["payloads"]


def _find_id_query_field(schema_response: dict) -> str | None:
    """Searches an introspection response for a query-type field with
    an `id` argument -- see module docstring's "IDOR VIA ID QUERIES" note.

    Args:
        schema_response: The parsed JSON body of an introspection query
            response (built with `INTROSPECTION_QUERY`).

    Returns:
        The first matching field's name, or `None` if introspection was
        disabled (no `__schema` in the response) or no field has an
        `id` argument.
    """
    schema = schema_response.get("data", {}).get("__schema") if isinstance(schema_response, dict) else None
    if not schema:
        return None
    query_type_name = (schema.get("queryType") or {}).get("name")
    for entry in schema.get("types") or []:
        if entry.get("name") != query_type_name:
            continue
        for field in entry.get("fields") or []:
            arg_names = [a.get("name", "").lower() for a in field.get("args") or []]
            if "id" in arg_names:
                return field.get("name")
    return None


@register("graphql_scanner")
class GraphQLScanner(BaseScanner):
    """Section 7.23. See module docstring for the three techniques and
    the schema-discovered IDOR approach."""

    def __init__(self, session, *, payloads: list[dict] | None = None) -> None:
        """
        Args:
            session: See `BaseScanner`.
            payloads: See `xss_scanner.XSSScanner`. Currently used only
                for the batching probe's batch size (`batching.
                batch_size`) -- introspection and IDOR use fixed query
                strings, not payload-file-driven content, since a
                GraphQL introspection query has one standard shape.
        """
        super().__init__(session)
        self._payloads = payloads if payloads is not None else _load_payloads()

    async def scan(self, target_url: str) -> list[ExploitCandidate]:
        """See module docstring.

        Args:
            target_url: See `BaseScanner.scan`. No precondition applies
                -- every candidate URL is tried, matching XXE's
                whole-body technique's endpoint-agnostic approach.

        Returns:
            0 to 3 `ExploitCandidate`s, one per technique that fired.
        """
        candidates: list[ExploitCandidate] = []

        schema_response = await self._query(target_url, INTROSPECTION_QUERY)
        if schema_response is not None and schema_response.get("data", {}).get("__schema"):
            candidates.append(self._candidate(target_url, None, INTROSPECTION_QUERY, json.dumps(schema_response)))

            id_field = _find_id_query_field(schema_response)
            if id_field is not None:
                idor_candidate = await self._probe_idor(target_url, id_field)
                if idor_candidate is not None:
                    candidates.append(idor_candidate)

        batching_entries = [p for p in self._payloads if p["technique"] == "batching"]
        if batching_entries:
            batching_candidate = await self._probe_batching(target_url, batching_entries[0]["batch_size"])
            if batching_candidate is not None:
                candidates.append(batching_candidate)

        return candidates

    async def _probe_idor(self, target_url: str, id_field: str) -> ExploitCandidate | None:
        """Queries `id_field` with two different ID values -- see
        module docstring's "IDOR VIA ID QUERIES" note."""
        for candidate_id in ("1", "2"):
            query = f'{{ {id_field}(id: "{candidate_id}") {{ __typename }} }}'
            response = await self._query(target_url, query)
            if response is not None and response.get("data", {}).get(id_field) is not None:
                return self._candidate(target_url, id_field, query, json.dumps(response))
        return None

    async def _probe_batching(self, target_url: str, batch_size: int) -> ExploitCandidate | None:
        """Sends a batch of `batch_size` harmless queries in one
        request -- see module docstring's "BATCHING ABUSE" note."""
        batch_body = json.dumps([{"query": "{__typename}"} for _ in range(batch_size)])
        response = await self.session.request(
            "POST", target_url, headers={"Content-Type": "application/json"}, content=batch_body
        )
        try:
            data = json.loads(response.text)
        except (json.JSONDecodeError, ValueError):
            return None
        if isinstance(data, list) and len(data) == batch_size:
            return self._candidate(target_url, None, f"batch of {batch_size} queries", response.text)
        return None

    async def _query(self, target_url: str, query: str) -> dict | None:
        """POSTs a single GraphQL `query` and returns the parsed JSON
        body, or `None` if the response isn't valid JSON."""
        body = json.dumps({"query": query})
        response = await self.session.request(
            "POST", target_url, headers={"Content-Type": "application/json"}, content=body
        )
        try:
            parsed = json.loads(response.text)
        except (json.JSONDecodeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def _candidate(self, target_url: str, parameter: str | None, payload: str, response_text: str) -> ExploitCandidate:
        return ExploitCandidate(
            vuln_type="graphql",
            endpoint=target_url,
            http_method="POST",
            parameter=parameter,
            detected_by="graphql_scanner",
            payload_used=payload,
            raw_response_snapshot=response_text[:RAW_RESPONSE_SNAPSHOT_CHARS],
            probe_correlation_id=None,
        )
