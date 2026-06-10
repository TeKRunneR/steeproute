#!/usr/bin/env python
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
# Reason: this talks to Chrome over the DevTools Protocol as JSON; `json.loads`
# surfaces every CDP response field as Any — the same external-boundary relaxation
# the production modules use for osmnx/rasterio payloads.
"""Capture map + elevation-profile thumbnails from a rendered steeproute report.

The README `## Gallery` (Story 8.3) shows, per example region, a screenshot of the
route-1 report's Leaflet map and its Chart.js elevation profile. Those panes only
exist once the report's JavaScript runs (the map fetches OpenTopoMap tiles; the
profile is a `<canvas>`), so they can't be produced by static HTML parsing — a real
browser has to render the page.

This drives a headless Chromium-family browser (Edge or Chrome, whichever is found)
over the DevTools Protocol using only the standard library plus `requests` (already a
project dependency, vendored via osmnx). It navigates to the report `file://` URL,
waits for the tiles and chart to paint, reads each element's bounding box, and asks
Chrome to capture a PNG clipped to that box — so the crop happens browser-side and no
image library is needed.

Usage:
    uv run python devtools/gallery_capture.py REPORT.html OUT_DIR [--prefix NAME]
        [--wait SECONDS] [--scale FLOAT] [--width PX]

Writes `OUT_DIR/<prefix>map.png` and `OUT_DIR/<prefix>profile.png`.

This is a dev/maintenance tool, not part of the shipped package; it is the
documented regeneration path for the committed gallery PNGs (see
`docs/examples/README.md`).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import sys
import time
from typing import Any, final

import requests

# --- Browser discovery ------------------------------------------------------

_WINDOWS_BROWSER_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _find_browser() -> str:
    for name in ("chrome", "chromium", "chromium-browser", "google-chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    for path in _WINDOWS_BROWSER_CANDIDATES:
        if pathlib.Path(path).exists():
            return path
    raise SystemExit("No Chrome/Edge/Chromium binary found for headless capture.")


# --- Minimal synchronous WebSocket client (RFC 6455, client side) -----------
#
# Just enough of the protocol to talk CDP to a local browser: text frames out
# (masked, as the spec requires for client->server), full frames in (server
# frames are never masked). No fragmentation is emitted; inbound fragmentation
# and control frames (ping/close) are handled defensively.


@final
class _WS:
    def __init__(self, url: str) -> None:
        # ws://host:port/path
        assert url.startswith("ws://")
        hostport, _, path = url[len("ws://") :].partition("/")
        host, _, port = hostport.partition(":")
        self._sock = socket.create_connection((host, int(port or "80")), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET /{path} HTTP/1.1\r\n"
            f"Host: {hostport}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(handshake.encode())
        self._buf = b""
        # Read past the handshake response headers.
        while b"\r\n\r\n" not in self._buf:
            self._buf += self._sock.recv(4096)
        _, _, self._buf = self._buf.partition(b"\r\n\r\n")

    def send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x81])  # FIN + text opcode
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("WebSocket closed mid-frame")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv_text(self) -> str:
        """Return the next text message (assembling continuation frames)."""
        message = b""
        while True:
            b0, b1 = self._recv_exact(2)
            fin = b0 & 0x80
            opcode = b0 & 0x0F
            masked = b1 & 0x80
            length = b1 & 0x7F
            if length == 126:
                (length,) = struct.unpack(">H", self._recv_exact(2))
            elif length == 127:
                (length,) = struct.unpack(">Q", self._recv_exact(8))
            mask = self._recv_exact(4) if masked else b""
            data = self._recv_exact(length)
            if masked:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x8:  # close
                raise ConnectionError("WebSocket closed by peer")
            if opcode == 0x9:  # ping -> ignore (browser rarely pings)
                continue
            message += data
            if fin:
                return message.decode("utf-8")

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


# --- CDP session ------------------------------------------------------------


@final
class _CDP:
    def __init__(self, ws_url: str) -> None:
        self._ws = _WS(ws_url)
        self._id = 0

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        msg_id = self._id
        self._ws.send_text(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        # Drain until our matching response arrives (skip async events).
        while True:
            msg = json.loads(self._ws.recv_text())
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} failed: {msg['error']}")
                return msg.get("result", {})

    def wait_event(self, method: str, timeout_s: float = 30.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            msg = json.loads(self._ws.recv_text())
            if msg.get("method") == method:
                return msg.get("params", {})
        raise TimeoutError(f"timed out waiting for CDP event {method}")

    def close(self) -> None:
        self._ws.close()


def _launch(
    browser: str, port: int, width: int, height: int, scale: float
) -> subprocess.Popen[bytes]:
    profile = pathlib.Path(os.environ.get("TEMP", "/tmp")) / f"gallery-chrome-{port}"
    args = [
        browser,
        "--headless=new",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        f"--window-size={width},{height}",
        f"--force-device-scale-factor={scale}",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "about:blank",
    ]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ws_url_for_page(port: int, timeout_s: float = 20.0) -> str:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            targets = requests.get(f"http://127.0.0.1:{port}/json", timeout=5).json()
            for t in targets:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]
        except Exception as exc:  # noqa: BLE001 - retry until the port is up
            last_err = exc
        time.sleep(0.3)
    raise SystemExit(f"Could not reach Chrome DevTools on port {port}: {last_err}")


def _rect(cdp: _CDP, selector: str) -> dict[str, float]:
    expr = (
        f"(() => {{ const el = document.querySelector({json.dumps(selector)}); "
        "if (!el) return null; const r = el.getBoundingClientRect(); "
        "return {x: r.x + window.scrollX, y: r.y + window.scrollY, "
        "width: r.width, height: r.height}; })()"
    )
    res = cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    value = res.get("result", {}).get("value")
    if not value:
        raise SystemExit(f"selector {selector!r} not found in report; raw response: {res}")
    return value


def _capture_canvas(cdp: _CDP, selector: str, out: pathlib.Path) -> int:
    """Write a Chart.js canvas' own pixels to PNG (no DOM clip).

    Disables the chart's animation, recomputes geometry without animation
    (`update('none')`), then forces a synchronous repaint with `draw()` before
    reading the canvas via `toDataURL`. In headless the load animation is unreliable
    — it often freezes the line mid-draw, and even `update('none')` defers the actual
    paint to a `requestAnimationFrame` that may not fire, so `toDataURL` would read
    the frozen partial canvas. `draw()` paints the full final state immediately. Do
    NOT call `resize()` here — it kicks off an animated resize that reintroduces the
    truncation. The elevation canvas draws only lines/text (no cross-origin images),
    so it is not tainted and `toDataURL` is permitted.
    """
    expr = (
        f"(() => {{ const c = document.querySelector({json.dumps(selector)}); "
        "if (!c) return null; "
        "const ch = (window.Chart && Chart.getChart) ? Chart.getChart(c) : null; "
        "if (ch) { ch.options.animation = false; ch.update('none'); ch.draw(); } "
        "return c.toDataURL('image/png'); })()"
    )
    res = cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    data_url = res.get("result", {}).get("value")
    if not data_url:
        raise SystemExit(f"canvas {selector!r} not capturable; raw response: {res}")
    raw = base64.b64decode(data_url.split(",", 1)[1])
    out.write_bytes(raw)
    return len(raw)


def _capture_clip(cdp: _CDP, clip: dict[str, float], scale: float, out: pathlib.Path) -> int:
    res = cdp.call(
        "Page.captureScreenshot",
        {
            "format": "png",
            "captureBeyondViewport": True,
            "clip": {
                "x": clip["x"],
                "y": clip["y"],
                "width": clip["width"],
                "height": clip["height"],
                "scale": scale,
            },
        },
    )
    data = base64.b64decode(res["data"])
    out.write_bytes(data)
    return len(data)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("report", type=pathlib.Path, help="route-1.html to capture")
    ap.add_argument("out_dir", type=pathlib.Path, help="directory for the PNGs")
    ap.add_argument("--prefix", default="", help="filename prefix, e.g. 'route-1-'")
    ap.add_argument("--wait", type=float, default=8.0, help="seconds to wait for tiles/chart")
    ap.add_argument("--scale", type=float, default=1.0, help="device + clip scale factor")
    ap.add_argument("--width", type=int, default=900, help="browser viewport width (px)")
    ap.add_argument("--port", type=int, default=9311, help="DevTools port")
    args = ap.parse_args()

    report = args.report.resolve()
    if not report.exists():
        raise SystemExit(f"report not found: {report}")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    browser = _find_browser()
    proc = _launch(browser, args.port, args.width, 1400, args.scale)
    cdp: _CDP | None = None
    try:
        cdp = _CDP(_ws_url_for_page(args.port))
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        file_url = report.as_uri()
        cdp.call("Page.navigate", {"url": file_url})
        try:
            cdp.wait_event("Page.loadEventFired", timeout_s=30)
        except TimeoutError:
            pass  # static report; proceed to the tile/chart settle wait anyway
        time.sleep(args.wait)  # let OpenTopoMap tiles + Chart.js finish painting

        total = 0
        # Map: a div of tiles + an SVG route overlay — clip-capture the DOM region.
        rect = _rect(cdp, "#map")
        map_out = args.out_dir / f"{args.prefix}map.png"
        size = _capture_clip(cdp, rect, args.scale, map_out)
        total += size
        print(
            f"wrote {map_out} ({size / 1024:.0f} KB, {rect['width']:.0f}x{rect['height']:.0f} css px)"
        )

        # Profile: a Chart.js <canvas>. Export the canvas bitmap directly instead of
        # screenshotting the DOM region — clip-capturing a responsive canvas in
        # headless drops part of the line (resize/devicePixelRatio race). Forcing a
        # resize + immediate redraw, then reading the canvas' own pixels, is exact.
        profile_out = args.out_dir / f"{args.prefix}profile.png"
        size = _capture_canvas(cdp, "#elevation-profile", profile_out)
        total += size
        print(f"wrote {profile_out} ({size / 1024:.0f} KB)")

        print(f"total {total / 1024:.0f} KB")
        return 0
    finally:
        if cdp is not None:
            cdp.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
