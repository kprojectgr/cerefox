#!/usr/bin/env python3
"""Generate a JWT for local PostgREST authentication.

Reads PGRST_JWT_SECRET and POSTGRES_USER from .env (or environment),
prints a signed JWT token to stdout. Paste the output into your .env
as CEREFOX_SUPABASE_KEY.

Usage:
    python scripts/generate_jwt.py
"""

import base64
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def main() -> None:
    # Try to load .env
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

    secret = os.environ.get("PGRST_JWT_SECRET", "")
    role = os.environ.get("POSTGRES_USER", "cerefox")

    if not secret:
        print("Error: PGRST_JWT_SECRET not set in .env or environment.", file=sys.stderr)
        print("Set it first, then run this script to generate the JWT.", file=sys.stderr)
        sys.exit(1)

    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({"role": role}).encode())
    signing_input = f"{header}.{payload}"
    signature = _b64url(
        hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    )
    token = f"{signing_input}.{signature}"

    print(token)
    print(f"\nAdd this to your .env:", file=sys.stderr)
    print(f"CEREFOX_SUPABASE_KEY={token}", file=sys.stderr)


if __name__ == "__main__":
    main()
