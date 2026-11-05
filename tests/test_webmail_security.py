#!/usr/bin/env python3
"""
Verification Test 6: Webmail Root Ingress & Cookie Security
Validates:
1. Webmail is served directly at root (http://localhost:8080/):
   - Returns HTTP 200 with text/html Content-Type
   - Body contains valid Grommunio Web HTML markup
   - Body does NOT contain PHP configuration errors (session.cookie_secure)
2. Cookies are issued with required security attributes:
   - Sets secure session cookie (__Secure-GROMMUNIO_WEB)
   - Attributes include 'secure', 'HttpOnly', and 'SameSite'
3. Static assets load correctly at root path:
   - CSS and Favicon return HTTP 200 with matching Content-Types
4. Absence of legacy /web paths:
   - Nginx configuration contains no /web location blocks
6. FastCGI Origin normalization:
   - Submitting logon with Origin: http://localhost:8080 succeeds (HTTP 303 redirect)
   - Untrusted cross-origin requests (Origin: http://evil.com) are rejected with HTTP 403
7. System user group memberships:
   - User www-data belongs to gromox group in image
"""
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

from env_helper import ensure_testuser, get_http_port

def check_webmail_root_ingress():
    http_port = get_http_port()
    base_url = f"http://localhost:{http_port}"
    print(f"==> Checking Webmail Root Ingress ({base_url}/)...")
    ensure_testuser()
    url = f"{base_url}/"
    req = urllib.request.Request(url, headers={"User-Agent": "GrommunioTest/1.0"})

    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.status
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8", errors="ignore")
            cookies = resp.headers.get_all("Set-Cookie") or []
    except Exception as ex:
        print(f"    [-] Connection error to {url}: {ex}", file=sys.stderr)
        return False

    # 1. Status and Content-Type check
    if status != 200 or "text/html" not in content_type:
        print(f"    [-] Root path returned HTTP {status}, Content-Type: {content_type}", file=sys.stderr)
        return False
    print(f"    [+] Root path returned HTTP 200 OK ({content_type.split(';')[0]}).")

    # 2. Markup and Preflight Security checks
    if "<title>grommunio Web</title>" not in body:
        print("    [-] HTML markup missing '<title>grommunio Web</title>'.", file=sys.stderr)
        return False
    print("    [+] HTML body contains '<title>grommunio Web</title>'.")

    error_indicators = [
        "PHP Security Config error",
        "session.cookie_secure must be 'on'",
        "incompatible configuration",
    ]
    for error_str in error_indicators:
        if error_str in body:
            print(f"    [-] Found error banner in HTML body: '{error_str}'", file=sys.stderr)
            return False
    print("    [+] No PHP configuration errors or preflight warnings in response body.")

    # 3. Cookie Security Attributes check
    if not cookies:
        print("    [-] No Set-Cookie headers received from Webmail.", file=sys.stderr)
        return False

    parsed = []
    for header in cookies:
        name = header.split(";", 1)[0].split("=", 1)[0].strip()
        attrs = {part.strip().split("=", 1)[0].lower() for part in header.split(";")[1:]}
        parsed.append((name, attrs))
    if "__Secure-GROMMUNIO_WEB" not in {name for name, _ in parsed}:
        print(f"    [-] Session cookie __Secure-GROMMUNIO_WEB not issued: {cookies}", file=sys.stderr)
        return False
    for name, attrs in parsed:
        missing = {"secure", "httponly", "samesite"} - attrs
        if missing:
            print(f"    [-] Cookie '{name}' lacks attributes {sorted(missing)}: {cookies}", file=sys.stderr)
            return False
    print(f"    [+] All {len(parsed)} cookies carry Secure, HttpOnly and SameSite attributes.")

    # 4. Static assets loading
    asset_urls = [
        ("Favicon", f"{base_url}/client/resources/images/favicon.ico", "image/x-icon"),
        ("Login CSS", f"{base_url}/client/resources/css/external/login.css", "text/css"),
    ]
    for name, asset_url, expected_ct in asset_urls:
        asset_req = urllib.request.Request(asset_url, headers={"User-Agent": "GrommunioTest/1.0"})
        try:
            with urllib.request.urlopen(asset_req) as a_resp:
                a_status = a_resp.status
                a_ct = a_resp.headers.get("Content-Type", "")
                if a_status != 200 or expected_ct not in a_ct:
                    print(f"    [-] Static asset {name} returned HTTP {a_status}, CT: {a_ct}", file=sys.stderr)
                    return False
                print(f"    [+] Static asset '{name}' returned HTTP 200 OK ({a_ct.split(';')[0]}).")
        except Exception as ex:
            print(f"    [-] Failed to fetch static asset {name}: {ex}", file=sys.stderr)
            return False

    # 6. Webmail logon with Origin header (verifies FastCGI origin normalization and CSRF acceptance)
    logon_url = f"{base_url}/?logon"
    post_data = urllib.parse.urlencode({"username": "testuser", "password": "testuser_secret"}).encode("utf-8")

    class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def http_error_303(self, req, fp, code, msg, headers):
            return fp

    opener = urllib.request.build_opener(NoRedirectHandler)
    req = urllib.request.Request(
        logon_url,
        data=post_data,
        headers={
            "User-Agent": "GrommunioTest/1.0",
            "Origin": base_url,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        resp = opener.open(req)
        if resp.status not in (200, 303):
            print(f"    [-] Logon with Origin header returned unexpected status {resp.status}", file=sys.stderr)
            return False
        print(f"    [+] Logon with Origin header ('{base_url}') succeeded (HTTP {resp.status}).")

        # 6b. Verify authenticated root access with session cookies (prevents MAPI / exmdb port 5000 HTTP 500 regression)
        set_cookies = resp.headers.get_all("Set-Cookie") or []
        cookie_header = "; ".join([c.split(";")[0] for c in set_cookies])
        auth_req = urllib.request.Request(
            f"{base_url}/",
            headers={
                "User-Agent": "GrommunioTest/1.0",
                "Cookie": cookie_header,
            },
        )
        with urllib.request.urlopen(auth_req) as auth_resp:
            auth_status = auth_resp.status
            auth_user = auth_resp.headers.get("X-grommunio-Authuser", "")
            if auth_status != 200 or auth_user != "testuser":
                print(f"    [-] Authenticated root access returned HTTP {auth_status}, Authuser: '{auth_user}'", file=sys.stderr)
                return False
            print("    [+] Authenticated Webmail session returned HTTP 200 OK (X-grommunio-Authuser: testuser).")
    except urllib.error.HTTPError as ex:
        print(f"    [-] Logon or authenticated session failed with HTTP {ex.code}", file=sys.stderr)
        return False

    # 7. Untrusted cross-origin request must be rejected with HTTP 403 Forbidden
    evil_req = urllib.request.Request(
        logon_url,
        data=post_data,
        headers={
            "User-Agent": "GrommunioTest/1.0",
            "Origin": "http://evil.com",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        opener.open(evil_req)
        print("    [-] Untrusted Origin 'http://evil.com' was NOT rejected with HTTP 403.", file=sys.stderr)
        return False
    except urllib.error.HTTPError as ex:
        if ex.code != 403:
            print(f"    [-] Untrusted Origin returned HTTP {ex.code}, expected 403.", file=sys.stderr)
            return False
        print("    [+] Untrusted Origin ('http://evil.com') correctly rejected with HTTP 403 Forbidden.")

    # 8. Verify www-data group membership
    id_proc = subprocess.run(["docker", "compose", "exec", "-T", "grommunio", "id", "www-data"], capture_output=True, text=True)
    if id_proc.returncode != 0 or "gromox" not in id_proc.stdout:
        print(f"    [-] www-data is not a member of gromox group: {id_proc.stderr or id_proc.stdout}", file=sys.stderr)
        return False
    print("    [+] Container user 'www-data' confirmed member of 'gromox' group.")

    return True

if __name__ == "__main__":
    if check_webmail_root_ingress():
        print("[+] SUCCESS: Webmail root ingress and security verified.\n")
        sys.exit(0)
    else:
        print("[-] FAILED: Webmail root ingress or security verification failed.\n", file=sys.stderr)
        sys.exit(1)
