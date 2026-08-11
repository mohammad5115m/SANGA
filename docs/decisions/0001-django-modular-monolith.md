# ADR-0001: Django-first modular monolith

## Status

Accepted

## Context

We need a production B2B/B2C stone inventory platform built by a small team, with excellent UX, strong tenancy/pricing security, and maintainability for a less-experienced owner-operator developer.

## Decision

Build a **modular Django monolith** using Django Templates + HTMX + Alpine.js + Tailwind. Use DRF only where APIs clearly help. Defer React/Next.js and microservices.

## Consequences

- Faster end-to-end feature delivery  
- Simpler deployment and debugging  
- UX quality depends on disciplined design system work (not admin skins)  
- If a future mobile native client needs a large public API, we can extract DRF endpoints gradually  
