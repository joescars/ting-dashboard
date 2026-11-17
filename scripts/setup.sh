#!/usr/bin/env bash
# One-command local setup: .env, data dir, a generated dashboard password,
# and the image build. No Ting credentials are handled here — link the
# account in the browser (or fill TING_EMAIL/TING_PASSWORD into .env).
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
port() { grep -E '^TING_PORT=' .env | cut -d= -f2 | tr -d '[:space:]' || echo 8097; }

if [[ -f .env ]]; then
  say ".env already exists — leaving it alone"
else
  cp .env.example .env
  pw="$(python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(20)))')"
  python3 - "$pw" <<'PY'
import pathlib, sys
pw = sys.argv[1]
p = pathlib.Path(".env")
out = [("DASHBOARD_PASSWORD=" + pw) if l.startswith("DASHBOARD_PASSWORD=") else l
       for l in p.read_text().splitlines()]
p.write_text("\n".join(out) + "\n")
PY
  chmod 600 .env
  say "Created .env with a generated dashboard password"
fi

mkdir -p data && chmod 700 data

# The container runs as the invoking user so the bind-mounted data dir is
# writable and auth.json stays owned by you.
uid=$(id -u); gid=$(id -g)
grep -q '^TING_UID=' .env || printf '\nTING_UID=%s\nTING_GID=%s\n' "$uid" "$gid" >> .env
sed -i "s/^TING_UID=.*/TING_UID=$uid/; s/^TING_GID=.*/TING_GID=$gid/" .env

say "Building the image (first build takes a minute)"
docker compose build -q

p="$(port)"
cat <<EOF

  Ready. Dashboard password:  $(grep -E '^DASHBOARD_PASSWORD=' .env | cut -d= -f2-)

  1. Start it:      docker compose up -d
  2. Link account:  open http://localhost:${p}/setup
                    and paste the code from
                    docker compose logs ting | grep -A2 "setup code"
     (skip the browser step if TING_EMAIL/TING_PASSWORD are in .env)
  3. Dashboard:     http://localhost:${p}
                    logs:   docker compose logs -f ting
                    stop:   docker compose down
                    update: docker compose pull && docker compose up -d
EOF
