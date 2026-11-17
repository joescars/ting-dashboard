# Security

## How this app protects your data

| Concern | Handling |
| --- | --- |
| Ting password | Used once for the Cognito sign-in, held in memory for that request, then discarded. Never written to disk or logs. |
| Ting session | Only the Cognito **refresh token** and an API key are persisted, in `data/auth.json` (mode `0600`). |
| Dashboard access | Optional `DASHBOARD_PASSWORD`; sessions are HMAC-signed cookies (`HttpOnly`, `SameSite=Lax`, `Secure` behind TLS), 30-day expiry, key stored in `data/session.key` (`0600`). |
| Brute force | 5 failed attempts per IP per 5 minutes, plus a 0.6 s delay on failure. |
| Account binding | `/api/setup` requires a one-time setup code from the container log, only works from private/loopback addresses, and stops working once an account is linked. |
| Cross-site posts | SameSite cookies plus an `Origin`/`Host` check on every mutating endpoint. |
| Browser hardening | Strict CSP with no inline script/style, no CDN, no third-party requests; `X-Frame-Options: DENY`, `nosniff`, restrictive `Referrer-Policy`. |
| Container | Non-root, `read_only` rootfs, `cap_drop: ALL`, `no-new-privileges`. Only `./data` is writable. |
| Telemetry | None. The only outbound traffic is to Whisker Labs' Cognito and API endpoints. |

## Deploying beyond your LAN

Put it behind a reverse proxy with TLS **and** set `DASHBOARD_PASSWORD`. If you
already run an SSO proxy (oauth2-proxy, Authelia, Cloudflare Access), you can
leave `DASHBOARD_PASSWORD` empty and let that proxy gate the whole service —
just make sure the container is not also published directly.

## Reporting a vulnerability

Open a private security advisory on the repository ("Security" → "Report a
vulnerability"). Please include a reproducer and the `v` number from the
dashboard footer. Expect an acknowledgement within a few days.

## Not a substitute for the Ting service

This dashboard reads status and voltage data; it does not replace Ting's own
alerts, the mobile app's notifications, or your insurance requirements. Do not
rely on it as your only fire-safety notification path.
