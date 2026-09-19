#!/usr/bin/env python3
"""Probe the test instance once a second and print a coloured, timestamped line (owner: M4).

    python demo/curl_loop.py <target-ip-or-host> [--port 80] [--timeout 1]

Green = HTTP 200 (service reachable). Red = timeout / refused / non-200 (service dark).
Stdlib only. Ctrl-C to stop; prints the longest outage seen.
"""
import argparse
import http.client
import os
import time
from datetime import datetime

GREEN, RED, DIM, RESET = "\033[1;32m", "\033[1;31m", "\033[2m", "\033[0m"


def probe(host, port, timeout):
    t0 = time.monotonic()
    try:
        c = http.client.HTTPConnection(host, port, timeout=timeout)
        c.request("GET", "/")
        r = c.getresponse()
        body = r.read(200).decode("utf-8", "replace").strip().replace("\n", " ")
        c.close()
        return r.status, body, time.monotonic() - t0
    except Exception as e:
        return None, type(e).__name__, time.monotonic() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--timeout", type=float, default=1.0)
    a = ap.parse_args()
    if os.name == "nt":
        os.system("")  # enable ANSI colours in Windows 10/11 consoles
    down_since, longest = None, 0.0
    try:
        while True:
            start = time.monotonic()
            status, info, dt = probe(a.target, a.port, a.timeout)
            ts = datetime.now().strftime("%H:%M:%S")
            if status == 200:
                if down_since is not None:
                    longest = max(longest, time.monotonic() - down_since)
                    down_since = None
                print(f"{DIM}{ts}{RESET}  {GREEN}200 OK       {RESET} {dt * 1000:5.0f} ms  {info[:60]}", flush=True)
            else:
                down_since = down_since or time.monotonic()
                label = f"{status}" if status else "TIMEOUT/ERR"
                print(f"{DIM}{ts}{RESET}  {RED}{label:<12}{RESET} {dt * 1000:5.0f} ms  {info[:60]}", flush=True)
            time.sleep(max(0, 1.0 - (time.monotonic() - start)))
    except KeyboardInterrupt:
        if down_since is not None:
            longest = max(longest, time.monotonic() - down_since)
        print(f"\nlongest outage seen: {longest:.0f} s")


if __name__ == "__main__":
    main()
