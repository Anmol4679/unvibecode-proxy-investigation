# UnvibeCode proxy-routing investigation

## 1. Overview

A small, local-only engineering reproduction of environment-proxy routing in UnvibeCode 0.4.0. It compares Python's standard urllib opener with the real installed `unvibecode.client.Client.request()` transport.

**This is independent supporting evidence for an existing report, not a new bug discovery.** The upstream report is [FinanceFlash/unvibecode#692](https://github.com/FinanceFlash/unvibecode/issues/692). This project contains no fix and does not modify UnvibeCode.

## 2. Issue reproduced

The 0.4.0 client always supplies an empty proxy dictionary when constructing its opener. With a reachable forward proxy and a deliberately unavailable direct route, standard urllib succeeds through the proxy while UnvibeCode selects the direct route and reports an API/SSH-tunnel reachability error.

The main test uses `https://api.proxy-test.invalid/analysis`, a synthetic non-loopback hostname. No real API or repository upload is involved.

## 3. Tested environment

| Component | Verified version |
| --- | --- |
| Operating system | Windows (`win32`) |
| Python | CPython 3.14.7 |
| UnvibeCode | 0.4.0 |

Other operating systems and Python versions have not been validated by this project. The script uses only the standard library and the pinned UnvibeCode package. Run without `-O`, since assertions verify expected observations.

## 4. Root cause

In the inspected 0.4.0 installation, `unvibecode/client.py:23` contains:

```python
self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
```

An empty dictionary disables automatic proxy discovery. `Client.request()` uses that opener at line 32; submit/status/download/interim delegate to the same request path. Python documents this behavior in its [ProxyHandler reference](https://docs.python.org/3.14/library/urllib.request.html#urllib.request.ProxyHandler).

The constructor permits HTTP only for loopback endpoints and mentions SSH tunneling (`client.py:21–22`). Keeping a localhost tunnel direct can be reasonable. It does not require disabling proxy discovery for public HTTPS endpoints. This distinction describes behavior, not a claim about the author's intent or an explicit documented proxy-support guarantee.

## 5. Expected versus actual

| Controlled case | Standard urllib | Installed Client.request() |
| --- | --- | --- |
| HTTPS_PROXY; synthetic non-loopback HTTPS target; direct route refused | Proxy CONNECT and JSON response succeed | Direct route selected; RuntimeError |
| HTTP_PROXY; localhost HTTP target; direct route refused | Absolute-form proxy request succeeds | Direct route selected; RuntimeError |
| NO_PROXY matches synthetic HTTPS target | Direct route selected and refused | Direct route selected and refused |
| NO_PROXY matches reachable localhost HTTP target | Direct request succeeds | Direct request succeeds |

The practical interoperability expectation is to allow configured proxy routing for hosted HTTPS while preserving localhost bypass. Non-loopback HTTP is rejected as designed and is not presented as a bug.

## 6. Reproduce

Create an environment using the desired Python interpreter:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -B -X utf8 proxy_request_repro.py
```

Dependency installation needs package-index access or a suitable local wheel cache. **The reproduction itself is local-only.** No application repository, account, real API token or proxy credentials are required.

Capture two runs in PowerShell:

```powershell
New-Item -ItemType Directory -Force results | Out-Null
.\.venv\Scripts\python.exe -B -X utf8 proxy_request_repro.py | Tee-Object results/run-1.txt
if ($LASTEXITCODE -ne 0) { throw 'Run 1 failed' }
.\.venv\Scripts\python.exe -B -X utf8 proxy_request_repro.py | Tee-Object results/run-2.txt
if ($LASTEXITCODE -ne 0) { throw 'Run 2 failed' }
```

## 7. Captured results

Fresh captures from this project are in [results/run-1.txt](results/run-1.txt) and [results/run-2.txt](results/run-2.txt). Both runs completed all assertions with exit code 0 and produced identical output.

Main comparison:

```text
HTTPS_PROXY remote | urllib | OK | proxy_hits=1 | direct_attempts=0
HTTPS_PROXY remote | Client.request | RuntimeError: Cannot reach API. Check the service and SSH tunnel. | proxy_hits=0 | direct_attempts=1
```

Final assertion summary:

```text
PASS: request-path assertions; loopback-only sockets; package unchanged
```

`remote` labels the synthetic hostname; it does not indicate a contacted remote server. `direct_attempts` counts route selection intercepted before external DNS/TCP, not outgoing external packets. HTTPS proxy hits count CONNECT; HTTP proxy hits count absolute-form GET. A passing reproduction confirms the expected difference, not successful hosted connectivity.

## 8. Developer impact

When the permitted route to the hosted API requires a forward proxy and direct egress is unavailable, the client's direct-only opener cannot use that configured route. The resulting SSH-tunnel diagnostic can misdirect troubleshooting for hosted-service users. This is a transport concern independent of repository content.

Actual enterprise connectivity and every client endpoint were not separately tested here. This reproduction does not establish a security vulnerability or explain any earlier TLS certificate error.

## 9. Safety and limitations

- Actual sockets connect only to an ephemeral server bound to `127.0.0.1`. Audit hooks reject non-loopback DNS and TCP.
- A controlled `socket.create_connection` wrapper refuses synthetic direct routes before DNS or TCP. It does not bypass an actual firewall or organizational restriction.
- The local HTTPS fixture performs real urllib CONNECT handling, but temporarily replaces TLS wrapping with the existing loopback socket. It then exchanges plaintext dummy HTTP within that fixture. **This is not a TLS/certificate test.** The context manager restores TLS wrapping afterward; no real service receives an unverified connection.
- Environment proxy values and REQUEST_METHOD are controlled per case, preserving other Windows variables and restoring the original environment. Windows registry/PAC behavior, authenticated proxies and SOCKS are outside scope.
- The package's request method, opener and ProxyHandler are not replaced. No installed package file is edited; the script verifies the client source hash before and after.
- Only an explicit dummy bearer value and synthetic JSON are used. No source code, personal paths, private repository data or real credentials are included in the captured logs.
- Audit hooks remain active until the Python process exits. Run this as a standalone script.

## 10. Upstream reference and attribution

[Upstream issue #692](https://github.com/FinanceFlash/unvibecode/issues/692) already reports environment proxies being disabled and the misleading SSH-tunnel error. This repository independently reproduces that existing mechanism with a controlled loopback fixture and additional bypass controls. It makes no original-discovery claim and does not reproduce every observation made in the upstream report.

No issue, pull request, form submission or publication is performed by the script.
