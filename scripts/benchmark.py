"""Local performance snapshot for OpenStudy.

Default requests query only lightweight endpoints. --include-llm intentionally generates
new tutor messages and may incur provider billing; use it only when you want that test.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import statistics
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def measure(base: str, path: str, *, times: int, payload: dict | None = None, authorization: str = "") -> dict:
    samples = []
    errors = []
    for _ in range(times):
        body = json.dumps(payload).encode('utf-8') if payload is not None else None
        req = Request(f'{base.rstrip("/")}{path}', data=body,
                      headers=({'Content-Type': 'application/json'} if body else {}) | ({'Authorization': authorization} if authorization else {}),
                      method='POST' if body else 'GET')
        started = time.perf_counter()
        try:
            with urlopen(req, timeout=360 if body else 15) as response:
                response.read()
                if not 200 <= response.status < 300:
                    raise RuntimeError(f'HTTP {response.status}')
                samples.append(round((time.perf_counter() - started) * 1000, 1))
        except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
            errors.append(str(exc)[:150])
    ordered = sorted(samples)
    return {
        'endpoint': path, 'successes': len(samples), 'errors': errors,
        'mean_ms': round(statistics.mean(samples), 1) if samples else None,
        'p50_ms': ordered[(len(ordered)-1)//2] if ordered else None,
        'p95_ms': ordered[max(0, (95*len(ordered)+99)//100 - 1)] if ordered else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description='Measure OpenStudy latency locally.')
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--repeat', type=int, default=5)
    parser.add_argument('--include-llm', action='store_true', help='Tests the live tutor. May use paid model tokens and writes chat history.')
    args = parser.parse_args()
    repeat = max(1, min(args.repeat, 20))
    # Prefer a secret from the environment, otherwise the bootstrap file. Never print it.
    password = os.environ.get("OPENSTUDY_ADMIN_PASSWORD", "")
    if not password and Path("data/.openstudy-admin-password").is_file():
        password = Path("data/.openstudy-admin-password").read_text(encoding="utf-8").strip()
    authorization = (
        "Basic " + base64.b64encode((os.getenv("OPENSTUDY_ADMIN_USER", "admin") + ":" + password).encode()).decode()
        if password else ""
    )
    checks = ['/api/stats', '/api/agents/status']
    results = [measure(args.url, path, times=repeat, authorization=authorization) for path in checks]
    if args.include_llm:
        payload = {'message': 'Resuma o que é recuperação ativa em duas frases.', 'language': 'pt-BR', 'mode': 'simple'}
        results.append(measure(args.url, '/api/tutor/chat', times=3, payload=payload, authorization=authorization))
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
