"""Pairing: build the phone URL and render a scannable QR code to the terminal."""

from __future__ import annotations

import io


def pairing_url(ip: str, port: int, token: str) -> str:
    return f"http://{ip}:{port}/?t={token}"


def qr_ascii(url: str) -> str:
    """Return the pairing URL as an ASCII QR code (prints fine in any terminal)."""
    try:
        import qrcode
    except ImportError:  # pragma: no cover - optional nicety
        return "(install 'qrcode' to show a scannable code)"

    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    return buf.getvalue()


def banner(ip: str, port: int, token: str) -> str:
    url = pairing_url(ip, port, token)
    return (
        "\n"
        "  Remote Presentation host is running.\n"
        f"  On your phone (same WiFi), open:  {url}\n"
        "  Or scan this QR code:\n\n"
        f"{qr_ascii(url)}\n"
        f"  Pairing token: {token}\n"
    )
