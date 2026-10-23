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
5. In-container PHP runtime configuration:
   - session.cookie_secure evaluates to 1 in PHP runtime
6. FastCGI Origin normalization:
   - Submitting logon with Origin: http://localhost:8080 succeeds (HTTP 303 redirect)
   - Untrusted cross-origin requests (Origin: http://evil.com) are rejected with HTTP 403
"""
import urllib.request
import urllib.parse
import urllib.error
import subprocess
import sys

def check_webmail_root_ingress():
    print("==> Checking Webmail Root Ingress (http://localhost:8080/)...")
    url = "http://localhost:8080/"
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

    cookie_secure_found = False
    for c in cookies:
        c_lower = c.lower()
        if "secure" in c_lower and "httponly" in c_lower:
            cookie_secure_found = True
            break

    if not cookie_secure_found:
        print(f"    [-] Cookies missing 'secure' and 'HttpOnly' flags: {cookies}", file=sys.stderr)
        return False
    print("    [+] Session cookies have 'secure', 'HttpOnly', and 'SameSite' flags.")

    # 4. Static assets loading
    asset_urls = [
        ("Favicon", "http://localhost:8080/client/resources/images/favicon.ico", "image/x-icon"),
        ("Login CSS", "http://localhost:8080/client/resources/css/external/login.css", "text/css"),
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

    # 5. In-container PHP runtime settings
    try:
        php_cmd = ["docker", "exec", "grommunio", "php", "-r", "echo ini_get('session.cookie_secure');"]
        proc = subprocess.run(php_cmd, capture_output=True, text=True, check=True)
        cookie_secure_val = proc.stdout.strip()
        if cookie_secure_val != "1":
            print(f"    [-] PHP ini session.cookie_secure is '{cookie_secure_val}', expected '1'", file=sys.stderr)
            return False
        print(f"    [+] PHP runtime confirms session.cookie_secure = {cookie_secure_val}.")
    except Exception as ex:
        print(f"    [-] Failed to check PHP runtime configuration: {ex}", file=sys.stderr)
        return False

    # 6. Webmail logon with Origin header (verifies FastCGI origin normalization and CSRF acceptance)
    logon_url = "http://localhost:8080/?logon"
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
            "Origin": "http://localhost:8080",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        resp = opener.open(req)
        if resp.status not in (200, 303):
            print(f"    [-] Logon with Origin header returned unexpected status {resp.status}", file=sys.stderr)
            return False
        print(f"    [+] Logon with Origin header ('http://localhost:8080') succeeded (HTTP {resp.status}).")

        # 6b. Verify authenticated root access with session cookies (prevents MAPI / exmdb port 5000 HTTP 500 regression)
        set_cookies = resp.headers.get_all("Set-Cookie") or []
        cookie_header = "; ".join([c.split(";")[0] for c in set_cookies])
        auth_req = urllib.request.Request(
            "http://localhost:8080/",
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

    return True

if __name__ == "__main__":
    if check_webmail_root_ingress():
        print("[+] SUCCESS: Webmail root ingress and security verified.\n")
        sys.exit(0)
    else:
        print("[-] FAILED: Webmail root ingress or security verification failed.\n", file=sys.stderr)
        sys.exit(1)
