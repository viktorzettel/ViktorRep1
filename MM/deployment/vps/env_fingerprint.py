#!/usr/bin/env python3
"""
Print non-secret fingerprints for required Polymarket environment values.

Use this to compare a local .env with the VPS .env without exposing keys.
Matching fingerprints mean the values are byte-for-byte identical after normal
.env parsing. The script never prints raw values.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from dotenv import dotenv_values


REQUIRED_KEYS = (
    "POLY_PRIVATE_KEY",
    "POLY_PROXY_ADDRESS",
    "POLY_API_KEY",
    "POLY_API_SECRET",
    "POLY_API_PASSPHRASE",
)


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def main() -> int:
    parser = argparse.ArgumentParser(description="Show safe .env fingerprints")
    parser.add_argument("--env-file", default=".env", help="Path to .env file")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    values = dotenv_values(env_path)
    print(f"env_file={env_path}")
    for key in REQUIRED_KEYS:
        value = values.get(key)
        if value is None:
            print(f"{key}=MISSING")
            continue
        text = str(value)
        if text == "":
            print(f"{key}=EMPTY")
            continue
        print(f"{key}=sha256:{fingerprint(text)} len:{len(text)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
