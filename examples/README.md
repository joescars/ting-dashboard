# Examples

`docker-compose.yml` — a self-contained stack for the **prebuilt image**
(`ghcr.io/joescars/ting-dashboard`), no clone-and-build needed. Copy it to your
server, put a `.env` beside it, and `docker compose up -d`.

The only setting you should change is `DASHBOARD_PASSWORD`. With it empty the
dashboard is readable by anyone who can reach the port — fine on a trusted LAN,
not fine if you forward the port.

## Behind a reverse proxy

The app speaks plain HTTP on 8097 and lets the proxy terminate TLS. Keep the port
bound to loopback so nothing reaches it except the proxy, and have the proxy set
`X-Forwarded-Proto: https` — the session cookie is then marked `Secure`
automatically:

```yaml
    ports:
      - "127.0.0.1:8097:8097"
```

Forwarded headers are only believed when the connection itself comes from
loopback, so a remote client cannot claim a private address in
`X-Forwarded-For` to reach `/setup`. Bind the proxy to 127.0.0.1 and this holds.

Caddy needs no extra config beyond `reverse_proxy 127.0.0.1:8097`; the app's
`/login`, `/api/*` and SSE stream all work through it.

## NAS and non-1000 users

`./data` must be writable by the container's uid/gid. Synology and QNAP accounts
are usually not uid 1000 — run `id <username>` on the host, then set
`TING_UID`/`TING_GID` in `.env`. Getting this wrong produces a startup error
telling you the exact `chown` to run, not a traceback.
