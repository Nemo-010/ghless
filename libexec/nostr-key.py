#!/usr/bin/env python3
"""Nostr key helper that needs only Python: used when `nak` cannot run (musl hosts).

usage: nostr-key.py gen            print a new 32-byte secret key as 64 hex characters
       nostr-key.py pub            read a hex secret on stdin, print the x-only public key (hex)
       nostr-key.py npub           read a hex secret on stdin, print the bech32 npub
"""
import os
import sys

P = 2**256 - 2**32 - 977
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
     0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)


def add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if a[0] == b[0] and (a[1] + b[1]) % P == 0:
        return None
    if a == b:
        lam = 3 * a[0] * a[0] * pow(2 * a[1], -1, P) % P
    else:
        lam = (b[1] - a[1]) * pow(b[0] - a[0], -1, P) % P
    x = (lam * lam - a[0] - b[0]) % P
    return x, (lam * (a[0] - x) - a[1]) % P


def mul(k, point=G):
    result = None
    while k:
        if k & 1:
            result = add(result, point)
        point = add(point, point)
        k >>= 1
    return result


CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def polymod(values):
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    return chk


def bech32(hrp, data):
    bits, acc, out = 0, 0, []
    for byte in data:
        acc = (acc << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            out.append((acc >> bits) & 31)
    if bits:
        out.append((acc << (5 - bits)) & 31)
    expanded = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
    mod = polymod(expanded + out + [0] * 6) ^ 1
    checksum = [(mod >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(CHARSET[d] for d in out + checksum)


def read_secret():
    text = sys.stdin.read().strip()
    k = int(text, 16)
    if not 0 < k < N or len(text) != 64:
        raise SystemExit("secret must be 64 hex characters in the curve order")
    return k


def main():
    cmd = sys.argv[1] if len(sys.argv) == 2 else ""
    if cmd == "gen":
        while True:
            k = int.from_bytes(os.urandom(32), "big")
            if 0 < k < N:
                print(f"{k:064x}")
                return 0
    if cmd in ("pub", "npub"):
        x = mul(read_secret())[0].to_bytes(32, "big")
        print(x.hex() if cmd == "pub" else bech32("npub", x))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
