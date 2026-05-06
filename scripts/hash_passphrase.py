#!/usr/bin/env python3
"""Print SHA-256 hex of a passphrase for iterthink/licensing.py LICENSE_PASSPHRASE_SHA256.

Matches iterthink.licensing._sha256: UTF-8 encoding and str.strip() before hashing.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import sys


def _sha256_hex(passphrase: str) -> str:
    return hashlib.sha256(passphrase.strip().encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hash a commercial license passphrase (SHA-256 hex, same rules as iterthink.licensing)."
    )
    parser.add_argument(
        "passphrase",
        nargs="?",
        help="Passphrase (visible in shell history). If omitted, prompts twice with no echo.",
    )
    parser.add_argument(
        "-s",
        "--stdin",
        action="store_true",
        help="Read passphrase from stdin (whole buffer; one trailing newline stripped).",
    )
    args = parser.parse_args()

    if args.stdin and args.passphrase:
        print("Use either a positional passphrase or --stdin, not both.", file=sys.stderr)
        sys.exit(2)

    if args.stdin:
        raw = sys.stdin.read()
        phrase = raw[:-1] if raw.endswith("\n") else raw
    elif args.passphrase is not None:
        phrase = args.passphrase
    else:
        a = getpass.getpass("Passphrase: ")
        b = getpass.getpass("Again: ")
        if a != b:
            print("Passphrases do not match.", file=sys.stderr)
            sys.exit(1)
        phrase = a

    print(_sha256_hex(phrase))


if __name__ == "__main__":
    main()
