# ADR-0003: OTP authentication with SMS provider abstraction

## Status

Accepted

## Context

Primary users are mobile-first in Iran. SMS OTP is the natural login method, but vendor lock-in and local-dev friction are real risks.

## Decision

- Phone-based custom user model  
- OTP challenge flow with rate limiting  
- `SmsProvider` interface with `ConsoleSmsProvider` (development) and swappable production providers  
- Never call a specific Iranian SMS vendor from domain services directly  

## Consequences

- Developers can log in without SMS credits  
- Production provider can be replaced via settings  
- Must carefully secure OTP attempt throttling and challenge expiry  
