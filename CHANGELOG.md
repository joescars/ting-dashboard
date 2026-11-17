# Changelog

All notable changes to this project. It follows
[Semantic Versioning](https://semver.org/): breaking configuration or storage
changes bump the major version.

## [1.0.0] — 2026-11-17

First public release.

### Added
- Browser-based onboarding: link a Ting account at `/setup` with a one-time
  code from the container log, no credentials in `.env` required.
- Optional `DASHBOARD_PASSWORD` with HMAC-signed session cookies, login rate
  limiting, and `Origin` checks on mutations.
- `scripts/setup.sh` one-command install: generates a dashboard password,
  prepares `./data`, and builds the image.
- PWA manifest and icons (installable on iOS/Android/desktop), version in the
  footer, ⋮ menu with *Sign out* and *Unlink Ting account*.
- `/api/session`, `/api/version`, `/api/unlink`; `/manifest.webmanifest`.
- Signal strip: THD, line frequency, and peak ("hi-fi") count taken from the
  voltage frame's extra fields; unknown scalar fields are shown under their raw
  name, and `/api/state` exposes them as `signal`.
- The 5-minute live chart is seeded from stored history, so a freshly opened
  page is drawn full instead of filling in from the right.
- Actionable startup error when the data directory is not writable.
- CI (tests, JS syntax, container smoke test) and a multi-arch release workflow.

### Changed
- Container image now carries OCI labels and a `TING_VERSION` build arg.
- Compose file adds a healthcheck, log rotation, and `TING_IMAGE`/`TING_VERSION`.
- `.env.example` documents every setting with comments on their own lines.

### Security
- Setup endpoint restricted to private/loopback peers and disabled once an
  account is linked.
- Dashboard password never appears in logs; the Ting password is discarded after
  the initial Cognito sign-in.

## [0.9.0] — 2026-11-17

Pre-release used for local development.

- Cloud-only polling, SignalR voltage stream, SQLite history, SSE fan-out.
- Material 3 UI with live chart, hazard tiles, history ranges, alerts.
- Demo mode (`TING_DEMO=1`) for previewing without an account.
- 9 tests including a fake SignalR hub.
