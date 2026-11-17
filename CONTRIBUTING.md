# Contributing

Thanks for helping. This is a small, dependency-light project on purpose —
please keep it that way.

## Ground rules

- **No new runtime dependencies** without a strong reason. The server is
  `aiohttp` + `msgpack`; the frontend is vanilla JS with hand-rolled canvas
  charts and no CDN assets. This keeps the container small and the CSP strict.
- **Never log, persist, or echo** a Ting password, access token, or API key.
  Only the refresh token belongs on disk, in `data/auth.json` (`0600`).
- Keep the Material 3 tokens in `static/app.css` as the single source of colour;
  components reference roles (`--primary`, `--on-surface`, …), never hex values.
- Anything user-visible needs a light *and* dark check.

## Getting set up

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q          # must stay green
TING_DEMO=1 TING_DATA_DIR=./data .venv/bin/python -m app.main
```

Demo mode needs no account and generates synthetic voltage, including a 24-hour
backfill, so the history charts have something to draw.

## Before opening a PR

1. `python -m pytest -q` passes.
2. `node --check static/app.js static/auth.js` (or open the page — the smoke
   test in CI covers the container path).
3. New behaviour has a test. The suite uses a fake SignalR hub and never
   touches the network; keep it that way.
4. If you touched configuration, update both `.env.example` and the table in
   `README.md`.
5. Describe the user-visible change and how you verified it in the PR body.

## Reporting issues

Include the version from the dashboard footer (⋮ menu), your platform
(Linux HAOS/Unraid/Raspberry Pi/macOS), and the relevant container logs with
secrets removed:

```bash
docker compose logs ting | grep -v -i "password\|token\|api.key"
```

Never paste a real email/password/token into an issue.

## Architecture notes

| File | Responsibility |
| --- | --- |
| `app/auth.py` | Cognito SRP sign-in, token refresh (adapted from ha-whisker-ting) |
| `app/client.py` | REST client, token lifecycle, `AuthStore` (0600 JSON) |
| `app/protocol.py` | SignalR MessagePack framing (adapted, MIT attributed) |
| `app/stream.py` | Reconnecting stream client, stale detection, backoff |
| `app/store.py` | SQLite schema, 5 s buckets, retention, downsampling |
| `app/authz.py` | Dashboard password sessions, setup codes, private-IP check |
| `app/main.py` | aiohttp app, routes, SSE fan-out, demo generator |
| `static/app.js` | SSE client, canvas charts, theme, range and device controls |
| `static/auth.js` | Shared controller for the login and setup pages |

The SignalR client and the protocol parser are the only parts coupled to
Whisker Labs' wire format; everything else is ours.
