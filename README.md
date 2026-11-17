# Ting Dashboard

Self-hosted web dashboard for a Whisker Labs **Ting** electrical fire sensor, replacing the
iOS app with something you can open on any screen in the house.

- **Live line voltage** chart (~4 samples/s) over a server-sent-events stream
- **Hazard status** tiles: fire alert, electrical + utility fire hazard, power quality,
  frozen-pipe risk, learning mode
- **History** in SQLite (1h / 6h / 24h / 7d / 30d) with avg/min/max/peak/dip stats
- **Alerts & notifications** from Ting's history API, plus a log of status changes
- Material 3 UI, light + dark, no CDN assets, strict CSP

## How it works

Ting is **cloud-only**: the sensor itself exposes no local services (full TCP scan and
UDP probes come back empty), so all data comes from Whisker Labs' unofficial API. The
protocol here is adapted from the community Home Assistant integration
[simplytoast1/ha-whisker-ting](https://github.com/simplytoast1/ha-whisker-ting) (MIT,
see `THIRD_PARTY_LICENSE_ha-whisker-ting`):

1. AWS Cognito SRP sign-in (`us-east-1` user pool) → access + refresh token
2. REST `GET api.wskr.io/api/v1/Users/{id}` → devices, hazard status, learning mode
3. SignalR MessagePack hub `wss://signalr.api.wskr.io/dataHub` → live voltage frames

This is not a public API and Whisker Labs can change it at any time.

## Setup

```bash
git clone <this repo> ting-dashboard && cd ting-dashboard
cp .env.example .env       # fill in TING_EMAIL / TING_PASSWORD
docker compose up -d --build
```

Open http://localhost:8097 (or `http://<lan-ip>:8097` from another device).

### Credentials

The password is used **once**, at first start, and is never written to disk. Only the
Cognito **refresh token** is persisted, in `data/auth.json` (mode `0600`). After the
first successful start you can delete `TING_PASSWORD` from `.env`.

To avoid putting the password in `.env` at all, sign in interactively instead:

```bash
docker compose run --rm ting python -m app.login
```

If the refresh token is ever revoked (password change, ~30-day rotation), the dashboard
shows a "Not signed in" banner and you repeat one of the two steps above.

### Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `TING_PORT` | `8097` | host port |
| `TING_POLL_INTERVAL` | `60` | seconds between status polls |
| `TING_NOTIFICATION_INTERVAL` | `300` | seconds between alert-history fetches |
| `TING_BUCKET_SECONDS` | `5` | voltage history resolution |
| `TING_RETENTION_DAYS` | `90` | voltage history retention |
| `TING_DEMO` | `0` | `1` = synthetic data, no account needed |
| `TING_UID` / `TING_GID` | `1000` | container user, must own `./data` |
| `LOG_LEVEL` | `INFO` | `DEBUG` logs decoded payload fields |

### Demo mode

```bash
docker run --rm -p 8097:8097 -e TING_DEMO=1 -e TING_DATA_DIR=/tmp/d ting-dashboard:latest
```

Generates synthetic voltage and a 24h backfill; every API response and the UI banner are
marked as demo data.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/state` | auth state, devices, hazard status, stream state |
| `GET /api/recent?station=SERIAL` | last 5 min of samples |
| `GET /api/history?station=SERIAL&range=1h\|6h\|24h\|7d\|30d` | bucketed downsampled history |
| `GET /api/events?station=SERIAL` | notifications + status-change log |
| `GET /api/live` | SSE: `state`, `sample` |
| `GET /healthz` | liveness |

Read-only by design: no endpoint mutates Ting state.

## Security notes

- Bind to LAN only; there is **no authentication** on the dashboard. Do not port-forward it.
- Secrets live in `.env` (gitignored) and `data/auth.json` (0600, gitignored).
- Container runs as a non-root user, read-only rootfs, all capabilities dropped.
- The password is never logged; API errors are reported without tokens.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q        # 9 tests, incl. a fake SignalR hub
.venv/bin/python -m app.main         # TING_DATA_DIR=./data TING_DEMO=1
```
