# UnvibeCode Proxy Routing Reproduction

## Overview

This repository provides a minimal, local-only reproduction of proxy routing behavior in UnvibeCode 0.4.0 (`unvibecode.client.Client.request()`) compared to Python's standard `urllib.request`.

With `HTTPS_PROXY` or `HTTP_PROXY` configured in the environment, standard `urllib` routes requests through the proxy. UnvibeCode's client bypasses configured proxies, attempts direct socket connections, and raises an SSH-tunnel error if direct access is unavailable.

This repository provides independent supporting evidence for upstream issue [#692](https://github.com/FinanceFlash/unvibecode/issues/692). It does not claim to be a new bug discovery and contains no package modifications.

Tested on Windows (`win32`) with CPython 3.14.7 and UnvibeCode 0.4.0.

## Reproduction

1. Set up a virtual environment and install the pinned package:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

2. Run the reproduction script:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 proxy_request_repro.py
```

The script runs four controlled cases against an ephemeral loopback server and exits with code 0 if all assertions pass:

```text
unvibecode=0.4.0; python=3.14.7; platform=win32
HTTPS_PROXY remote | urllib | OK | proxy_hits=1 | direct_attempts=0
HTTPS_PROXY remote | Client.request | RuntimeError: Cannot reach API. Check the service and SSH tunnel. | proxy_hits=0 | direct_attempts=1
HTTP_PROXY local HTTP | urllib | OK | proxy_hits=1 | direct_attempts=0
HTTP_PROXY local HTTP | Client.request | RuntimeError: Cannot reach API. Check the service and SSH tunnel. | proxy_hits=0 | direct_attempts=1
HTTPS NO_PROXY control | urllib | URLError: <urlopen error Synthetic direct route disabled; no DNS/TCP attempted> | proxy_hits=0 | direct_attempts=1
HTTPS NO_PROXY control | Client.request | RuntimeError: Cannot reach API. Check the service and SSH tunnel. | proxy_hits=0 | direct_attempts=1
HTTP NO_PROXY local control | urllib | OK | proxy_hits=0 | direct_attempts=1
HTTP NO_PROXY local control | Client.request | OK | proxy_hits=0 | direct_attempts=1
non-loopback HTTP URL: rejected as designed
PASS: request-path assertions; loopback-only sockets; package unchanged
```

Pre-captured run logs are saved in `results/run-1.txt` and `results/run-2.txt`.

## Expected vs Actual

| Scenario | Standard `urllib` | `unvibecode.client.Client.request()` |
| --- | --- | --- |
| `HTTPS_PROXY` set, synthetic target, direct route disabled | Connects to proxy via `CONNECT`; succeeds | Bypasses proxy; attempts direct connection; fails with `RuntimeError` |
| `HTTP_PROXY` set, localhost target, direct route disabled | Connects to proxy via absolute-form `GET`; succeeds | Bypasses proxy; attempts direct connection; fails with `RuntimeError` |
| `NO_PROXY` set for target | Directly connects to target; fails as route disabled | Directly connects to target; fails with `RuntimeError` |
| `NO_PROXY` set for reachable localhost target | Directly connects to target; succeeds | Directly connects to target; succeeds |

The expected behavior for standard Python CLI tools is to honor `HTTP_PROXY` and `HTTPS_PROXY` for remote endpoints while allowing `NO_PROXY` to bypass local destinations.

## Root Cause

In `unvibecode/client.py`, line 23:

```python
self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
```

Passing an empty dictionary `{}` to `urllib.request.ProxyHandler` disables automatic proxy discovery in Python's standard library. As documented in Python's [ProxyHandler documentation](https://docs.python.org/3/library/urllib.request.html#urllib.request.ProxyHandler), an empty dictionary causes `build_opener` to discard proxy handlers and ignore all environment variables (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`).

While lines 21–22 permit HTTP for localhost to support local SSH tunneling, using `ProxyHandler({})` globally prevents proxy resolution for external HTTPS endpoints (`https://shipready-api.alphashots.ai/analysis`).

## Limitations

- **Local-only execution**: All requests are handled by an ephemeral loopback socket (`127.0.0.1`). Python audit hooks (`sys.addaudithook`) reject any non-loopback DNS or TCP connections.
- **Mocked TLS**: The HTTPS test exercises Python's standard proxy `CONNECT` logic, but substitutes loopback plaintext inside the fixture rather than performing real TLS handshakes against a live proxy.
- **Scope**: Authenticated proxies, SOCKS proxies, and Windows registry/PAC configurations were not tested.

## Upstream Issue

This issue is tracked upstream in [FinanceFlash/unvibecode#692](https://github.com/FinanceFlash/unvibecode/issues/692). This reproduction provides an isolated, deterministic test case for regression verification.
