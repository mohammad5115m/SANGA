# ADR-0004: PostgreSQL search first

## Status

Accepted

## Context

Persian search quality matters, but introducing Meilisearch/Elasticsearch immediately adds ops burden before core workflows exist.

## Decision

Implement a `SearchService` abstraction with a PostgreSQL-backed implementation first (icontains/trigram + normalization + aliases). Design call sites so Meilisearch can later replace the backend.

**Current status:** the ADR stands; the `SearchService` interface and
`PostgresSearchService` are **not implemented yet**. List screens use ordinary
ORM filters today.

## Consequences

- Lower infrastructure complexity for v1  
- Good enough for early inventory sizes  
- Need careful normalization for ی/ك/ZWNJ when the service lands  
- May revisit when marketplace scale demands it  
