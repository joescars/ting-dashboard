"""Interactive sign-in: stores only the Cognito refresh token in the data dir.

    docker compose run --rm ting python -m app.login
"""

import asyncio
import getpass
import os
import sys

import aiohttp

from . import const
from .client import AuthStore, NotSignedIn, TingApiError, TingClient


async def _main() -> int:
    email = os.environ.get("TING_EMAIL") or input("Ting account email: ").strip()
    password = getpass.getpass("Ting password (not stored): ")
    async with aiohttp.ClientSession() as session:
        client = TingClient(session, AuthStore(const.DATA_DIR))
        try:
            await client.sign_in(email, password)
            user = await client.get_user()
        except (NotSignedIn, TingApiError) as err:
            print(f"Sign-in failed: {err}", file=sys.stderr)
            return 1
    names = ", ".join(d["name"] for d in user["devices"]) or "no devices"
    print(f"Signed in. Refresh token saved to {const.DATA_DIR}/auth.json. Devices: {names}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
