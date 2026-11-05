"""Local-network helpers: find the LAN IP the phone should connect to."""

from __future__ import annotations

import socket


def get_lan_ip() -> str:
    """Best-effort primary LAN IPv4 address.

    Opens a throwaway UDP socket toward a public address so the OS picks the
    outbound interface; no packet is actually sent. Falls back to loopback.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()
