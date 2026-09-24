# Robinhood Equity Pagination Contract

Issue #28 documents and implements the live Robinhood Trading MCP pagination
contract used by the V1 account snapshot path.

The contract was captured from the authenticated Trading MCP tool schemas on
2026-09-24. The relevant tools are `get_equity_positions` and
`get_equity_orders`.

## Request contract

For both tools:

- omit `cursor` on the first request;
- when a response contains a non-empty `data.next`, call the same tool again;
- pass the prior `data.next` value back verbatim as `cursor`;
- keep every other request argument unchanged;
- treat `data.next` as an opaque token, not a URL;
- pagination is complete only when `data.next` is empty or absent.

`get_equity_orders` single-order mode still uses
`account_number + order_id`. The weekly account snapshot uses list mode and
therefore consumes every page.

## V1 implementation safeguards

The deterministic gateway:

- uses the live `data.next -> cursor` contract only;
- caps retrieval at 100 pages;
- caps retrieval at 10,000 rows per collection;
- fails closed on repeated cursors;
- fails closed on malformed cursors;
- fails closed on unsupported/ambiguous pagination markers;
- requires a stable identity for every row;
- rejects duplicate order IDs across pages;
- rejects duplicate position symbols across pages;
- preserves raw page responses under pagination provenance;
- preserves the original MCP response envelope for a one-page collection;
- synthesizes a canonical combined response only after multi-page completeness
  has been proven.

Exact-order post-fill refresh remains narrow. If Robinhood unexpectedly
advertises pagination for an exact order lookup, the lookup fails closed rather
than silently accepting the first row.

## Scope boundary

This change is operational data-retrieval hardening only. It does not change:

- `long_growth_v1` factors, weights, eligibility, ranking, or Top-10 selection;
- portfolio allocation;
- order-review or order-placement semantics;
- explicit live approval;
- V2 research/shadow behavior.
