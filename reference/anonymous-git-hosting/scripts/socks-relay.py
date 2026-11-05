#!/bin/bash
# socks-relay.py: TCP relay through a Tor SOCKS5 port (or any SOCKS5).
# Use when your host cannot dial a port directly (allowlist proxies, port 8776 etc.).
#
#   python3 socks-relay.py <listen-port> <dest-host> <dest-port> [socks-host] [socks-port]
#
# Example: expose radicle seed port 8776 locally
#   python3 socks-relay.py 18776 iris.radicle.xyz 8776 127.0.0.1 9050
#   rad node connect <seed-nid>@127.0.0.1:18776
import socket, struct, threading, sys

def dial_via_socks(host, port, socks):
    s = socket.create_connection(socks, timeout=30)
    s.sendall(b'\x05\x01\x00'); s.recv(2)
    a = host.encode()
    s.sendall(b'\x05\x01\x00\x03' + bytes([len(a)]) + a + struct.pack('>H', port))
    r = s.recv(10)
    if r[1] != 0:
        raise OSError(f"socks reply {r[1]}")
    return s

def pump(a, b):
    try:
        while True:
            d = a.recv(65536)
            if not d: break
            b.sendall(d)
    except Exception:
        pass
    try: b.shutdown(socket.SHUT_WR)
    except Exception: pass

def handle(c, host, port, socks):
    try:
        u = dial_via_socks(host, port, socks)
    except Exception:
        c.close(); return
    t1 = threading.Thread(target=pump, args=(c, u), daemon=True)
    t2 = threading.Thread(target=pump, args=(u, c), daemon=True)
    t1.start(); t2.start(); t1.join(); t2.join(); c.close(); u.close()

def main():
    listen = ('127.0.0.1', int(sys.argv[1]))
    host, port = sys.argv[2], int(sys.argv[3])
    socks = (sys.argv[4] if len(sys.argv) > 4 else '127.0.0.1',
             int(sys.argv[5]) if len(sys.argv) > 5 else 9050)
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(listen); srv.listen(16)
    print(f"relay {listen[0]}:{listen[1]} -> {host}:{port} via socks {socks}", flush=True)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c, host, port, socks), daemon=True).start()

if __name__ == '__main__':
    main()
