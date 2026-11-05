"""
http_client.py — the one place every tool reaches the internet through.

Real networks fail. So every outbound call gets, in one shared spot:
  • a timeout (never hang the loop),
  • retries with exponential backoff,
  • explicit handling of 429 (rate limit / quota),
  • failures raised as a single HttpError type the tools can reason about.

Putting this here (not in each tool) means resilience is uniform and provable.
"""
import time
import requests

from config import cfg


class HttpError(Exception):
    """Any outbound call that ultimately failed (after retries)."""


def request(method, url, *, params=None, json=None, headers=None,
            timeout=None, retries=None, backoff=0.6):
    timeout = cfg.HTTP_TIMEOUT if timeout is None else timeout
    retries = cfg.HTTP_RETRIES if retries is None else retries
    last = None

    for attempt in range(retries + 1):
        try:
            r = requests.request(method, url, params=params, json=json,
                                  headers=headers, timeout=timeout)
            # 429 = rate limited / quota: wait and retry (don't give up immediately).
            if r.status_code == 429:
                last = HttpError("429 rate limited")
                time.sleep(backoff * (2 ** attempt))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last = e
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))   # 0.6s, 1.2s, 2.4s…

    raise HttpError(f"{method} {url} failed after {retries + 1} tries: {last}")
