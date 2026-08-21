# Security audit and hardening report

Date: 2026-08-20

## Scope

This review covered the local source trees for the Discord bot, website,
website backend, dashboard, documentation, AltGuard, Ozone, moddy-feeds and
moddysystems. It included manual trust-boundary review, dependency audits,
Semgrep, Bandit, Gitleaks, unit tests, type checks and production builds.

This was a source and local-build audit. It was not an authenticated test of
the production network, cloud accounts, databases, Discord application or CDN.

## Fixed findings

### Critical

1. The dashboard backend proxy accepted an arbitrary endpoint and signed the
   request with its server API key. It now exposes one exact allowlisted route,
   validates methods and payloads, restricts origins and applies timeouts.
2. A Discord webhook credential was committed in a test file. The working tree
   now reads it from `MODDY_TEST_WEBHOOK_URL`. The credential remains present in
   Git history and must be revoked and removed from history.
3. Ozone rendered untrusted email HTML with `dangerouslySetInnerHTML`. The HTML
   now passes through a strict server-side allowlist sanitizer.

### High

1. The bot internal API previously failed open without a configured secret. It
   now fails closed and compares credentials in constant time.
2. Starboard and bot-customization downloads allowed server-side requests to
   attacker-controlled destinations. URL parsing, DNS results, redirects,
   private addresses, ports, content types, sizes and timeouts are restricted.
3. Redis tasks capable of changing bot state were unsigned. They now use a
   timestamped HMAC contract and replay protection.
4. Website authentication accepted executable and external redirect targets.
   Redirects are now limited to safe same-origin paths.
5. Tally webhook routes accepted unsigned events. Valid signatures are now
   mandatory and sensitive signature values are not logged.
6. Stripe processing acknowledged events before completing them. Processing is
   now completed before acknowledgment and failed reservations are released so
   Stripe can retry.

### Medium and defense in depth

1. SVG uploads are parsed with hardened XML handling and validated content.
2. Bot image uploads validate file signatures instead of trusting MIME labels.
3. CORS, mutation-origin checks, no-store behavior and security headers were
   tightened across the web services.
4. OAuth state is single-use and session cookies have stricter defaults.
5. Sentry no longer sends default personally identifiable information.
6. Staff command audit logs no longer include source-code fragments.
7. Dynamic database identifiers are now exact allowlists.
8. AltGuard no longer embeds JSON inside an executable inline script.
9. Ozone proxy routes validate same-origin requests and strictly constrain
   remote URLs, payload sizes, record identifiers and request durations.
10. Abandoned or vulnerable dependency chains were upgraded or removed.

## Verification evidence

- Bot: 880 tests passed.
- Website backend: 41 security-focused tests passed. The full suite had also
  passed before the final two task-signing regression tests were added.
- AltGuard: 333 tests passed and 71 skipped.
- moddy-feeds: 16 tests passed.
- Website redirect regression tests passed.
- Dashboard lint, type checking and production build passed.
- Website, documentation, AltGuard and Ozone production builds passed.
- Ozone and backend type checks passed.
- npm and pip audits reported no known vulnerabilities in the audited active
  dependency sets. Ozone reports only the non-security deprecation of Recharts 2.
- Bandit reported no high-severity issue.
- The final focused Semgrep scan reports only the intentionally privileged
  developer `exec` command.

## Required deployment actions

1. Revoke the exposed Discord webhook immediately, create a replacement and
   update the secret store. Do not reuse the old URL.
2. Rewrite the affected Git history, then coordinate a fresh clone or safe
   reset for every contributor. Rotation is required even after rewriting.
3. Generate different secrets of at least 32 random bytes for
   `INTERNAL_API_SECRET` and `TASK_STREAM_SECRET`. Configure matching values on
   the bot and backend before deployment.
4. Configure the Tally signing secret and verify Stripe webhook secrets in the
   production secret store.
5. Confirm exact production origin allowlists and apply the supplied platform
   security headers at the CDN or reverse proxy.

## Residual risks and recommendations

1. The developer command intentionally executes Python supplied by a trusted
   bot developer. A compromised developer Discord account therefore becomes
   remote code execution. Require MFA, minimize the developer list, monitor its
   use and consider disabling it in production.
2. Ozone uses client-side AT Protocol authentication and its Next.js proxy
   routes do not have an independently authenticated server session. Current
   validation limits impact, but production should add edge authentication and
   rate limiting or move calls behind a verified server session.
3. Discord OAuth tokens are stored as plaintext values in Redis sessions.
   Protect Redis with private networking and TLS, restrict access, and consider
   envelope encryption with a managed key.
4. Verify TLS and authentication for Redis and PostgreSQL in the deployed
   environment. Source review cannot prove cloud network isolation.
5. The dashboard proxy target must be checked against the deployed backend.
   The allowlisted `/api/website/auth/init` route is not present in the local
   website-backend checkout.
6. Repeat dependency and static scans in CI, and add authenticated staging
   tests for access control, rate limits, webhook replay and security headers.

## Security posture

The confirmed directly exploitable source-level findings identified in this
review have been fixed in the working trees. Production safety still depends on
the deployment actions above, especially credential rotation, secret
configuration, network controls and privileged account security.
