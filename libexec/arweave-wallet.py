#!/usr/bin/env python3
"""Mint an Arweave RSA-4096 JWK wallet with openssl and print its address.

usage: arweave-wallet.py WALLET.json   (refuses to overwrite)
Prints the wallet address (base64url sha256 of the modulus) on stdout.
"""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys


def b64u(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def parse_text(text: str) -> dict:
    fields, name, buf = {}, None, []
    for line in text.splitlines():
        m = re.match(r"^(\w+):\s*(.*)$", line)
        if m:
            if name:
                fields[name] = "".join(buf)
            name, buf = m.group(1), [m.group(2)]
        elif name:
            buf.append(line.strip())
    if name:
        fields[name] = "".join(buf)
    out = {}
    for key, value in fields.items():
        value = value.replace(":", "").replace(" ", "")
        if key == "publicExponent":
            out[key] = int(re.match(r"\d+", fields[key]).group(0))
        elif re.fullmatch(r"[0-9a-f]+", value):
            out[key] = int(value, 16)
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    path = sys.argv[1]
    if os.path.exists(path):
        print(f"refusing to overwrite {path}", file=sys.stderr)
        return 1
    pem = subprocess.run(["openssl", "genrsa", "4096"], check=True,
                         capture_output=True).stdout
    text = subprocess.run(["openssl", "rsa", "-noout", "-text"], input=pem,
                          check=True, capture_output=True).stdout.decode()
    f = parse_text(text)
    need = ["modulus", "publicExponent", "privateExponent", "prime1", "prime2",
            "exponent1", "exponent2", "coefficient"]
    missing = [k for k in need if k not in f]
    if missing:
        print(f"could not parse openssl output, missing {missing}", file=sys.stderr)
        return 1
    if f["modulus"].bit_length() != 4096:
        print("modulus is not 4096 bits", file=sys.stderr)
        return 1
    jwk = {"kty": "RSA", "e": b64u(f["publicExponent"]), "n": b64u(f["modulus"]),
           "d": b64u(f["privateExponent"]), "p": b64u(f["prime1"]),
           "q": b64u(f["prime2"]), "dp": b64u(f["exponent1"]),
           "dq": b64u(f["exponent2"]), "qi": b64u(f["coefficient"])}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(jwk, fh)
    n_bytes = f["modulus"].to_bytes(512, "big")
    print(base64.urlsafe_b64encode(hashlib.sha256(n_bytes).digest()).rstrip(b"=").decode())
    return 0


if __name__ == "__main__":
    sys.exit(main())
