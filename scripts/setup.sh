#!/usr/bin/env bash
# One-time setup: prepares the data dir, .env and the container image.
# The Ting password is never handled here — see the final step it prints.
set -euo pipefail
cd "$(dirname "$0")/.."

[[ -f .env ]] || { cp .env.example .env; echo "created .env (edit TING_EMAIL if you like)"; }
mkdir -p data
chmod 700 data

# The container runs as the invoking user so the bind-mounted data dir is writable
# and auth.json stays owned by you.
uid=$(id -u); gid=$(id -g)
grep -q '^TING_UID=' .env || printf '\n# container user (must own ./data)\nTING_UID=%s\nTING_GID=%s\n' "$uid" "$gid" >> .env
sed -i "s/^TING_UID=.*/TING_UID=$uid/; s/^TING_GID=.*/TING_GID=$gid/" .env

docker compose build -q
echo
echo "Setup complete. Sign in (password is prompted, never stored):"
echo "    docker compose run --rm ting python -m app.login"
echo "Then start the dashboard:"
echo "    docker compose up -d"
echo "Open http://localhost:$(grep -E '^TING_PORT=' .env | cut -d= -f2 || echo 8097)"
