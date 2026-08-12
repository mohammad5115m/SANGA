# ADR-0001: Django-first modular monolith

## Status

Accepted

## Context

We need a production B2B/B2C stone inventory platform built by a small team, with excellent UX, strong tenancy/pricing security, and maintainability for a less-experienced owner-operator developer.

## Decision

Build a **modular Django monolith** using Django Templates + HTMX, with Alpine.js
available for local UI state and a hand-written RTL CSS design system
(`static/css/app.css`). Tailwind was considered and **deferred** (no Node front-end
build). Use DRF only where APIs clearly help. Defer React/Next.js and microservices.

## Consequences

- Faster end-to-end feature delivery  
- Simpler deployment and debugging (no Tailwind/Node pipeline)  
- UX quality depends on disciplined CSS-token / component work (not admin skins)  
- If a future mobile native client needs a large public API, we can extract DRF endpoints gradually  
