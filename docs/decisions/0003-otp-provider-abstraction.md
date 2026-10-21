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

## Implementation notes

`KavenegarSmsProvider` is the production adapter. Three choices are worth
recording, because each looks like an arbitrary detail and is not:

**The transactional `verify/lookup` endpoint, not the general send API.** It
takes a pre-approved template and substitutes one token, which is why the
provider extracts the code from the message body rather than sending the body.
The general API is treated as bulk traffic and is filtered outside daytime hours
— an OTP that arrives at 2am only if the recipient is lucky is not an OTP.

**`urllib`, not `requests`.** One POST to one endpoint does not justify an HTTP
dependency, and every dependency that touches credentials is one more thing to
keep patched.

**The API key travels in the URL path.** Nothing on this code path may log a URL,
and vendor exceptions are never chained through — `urllib.error.HTTPError`
stringifies to include its URL, so re-raising one as-is would put the key into
whatever caught it. `test_sms_provider.py` asserts that neither the key nor the
code appears in any log line on any failure path.

Production validates the credentials at import rather than at the first login
attempt, so a gateway configured by name but missing its key stops the deployment
instead of becoming an outage discovered by a user who cannot sign in.
