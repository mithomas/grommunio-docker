#!/usr/bin/env python3
"""
Verification Test: Outbound Smarthost Relay & Queue Spool Retention
Verifies the end-to-end outbound mail pipeline:
1. Active relay: submission on 127.0.0.1:2525 -> Exim outbound_relay -> remote_smtp -> RELAY_HOST:RELAY_PORT.
2. Outage retention: with smarthost down, messages stay spooled in Exim's queue (exim -bpc >= 1).
3. Recovery: once smarthost recovers, exim -qff delivers the queued messages and flushes the spool.
"""
import json
import secrets
import subprocess
import sys

from container_harness import Container, ScratchDatabase, _docker, _network, require_harness
from env_helper import load_env_secret, wait_until

CAPTURE_STUB = r'''
import asyncio
import json
import sys

class SmtpCaptureServer:
    async def handle_client(self, reader, writer):
        writer.write(b"220 capture.local ESMTP\r\n")
        await writer.drain()
        sender = ""
        recipients = []
        in_data = False
        data_lines = []
        while not reader.at_eof():
            line_bytes = await reader.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace")
            if in_data:
                if line == ".\r\n" or line == ".\n":
                    in_data = False
                    msg_data = "".join(data_lines)
                    log_entry = {
                        "from": sender,
                        "rcpts": recipients,
                        "data": msg_data,
                    }
                    print(json.dumps(log_entry), flush=True)
                    writer.write(b"250 OK: message queued\r\n")
                    await writer.drain()
                    sender = ""
                    recipients = []
                    data_lines = []
                else:
                    if line.startswith(".."):
                        line = line[1:]
                    data_lines.append(line)
            else:
                upper = line.upper()
                if upper.startswith("EHLO") or upper.startswith("HELO"):
                    writer.write(b"250-capture.local\r\n250 HELP\r\n")
                elif upper.startswith("MAIL FROM:"):
                    s = line.split(":", 1)[1].strip()
                    if s.startswith("<") and ">" in s:
                        s = s[1:s.index(">")]
                    sender = s
                    writer.write(b"250 OK\r\n")
                elif upper.startswith("RCPT TO:"):
                    r = line.split(":", 1)[1].strip()
                    if r.startswith("<") and ">" in r:
                        r = r[1:r.index(">")]
                    recipients.append(r)
                    writer.write(b"250 OK\r\n")
                elif upper.startswith("DATA"):
                    in_data = True
                    data_lines = []
                    writer.write(b"354 Start mail input; end with <CRLF>.<CRLF>\r\n")
                elif upper.startswith("RSET"):
                    sender = ""
                    recipients = []
                    data_lines = []
                    in_data = False
                    writer.write(b"250 OK\r\n")
                elif upper.startswith("NOOP"):
                    writer.write(b"250 OK\r\n")
                elif upper.startswith("QUIT"):
                    writer.write(b"221 Bye\r\n")
                    await writer.drain()
                    break
                else:
                    writer.write(b"500 Command unrecognized\r\n")
                await writer.drain()
        writer.close()
        await writer.wait_closed()

async def main():
    server = SmtpCaptureServer()
    srv = await asyncio.start_server(server.handle_client, "0.0.0.0", 2525)
    print("READY: SMTP capture server listening on port 2525", flush=True)
    async with srv:
        await srv.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
'''

SUBMIT_SCRIPT = r'''
import smtplib
import sys
from email.mime.text import MIMEText

sender = sys.argv[1]
rcpt = sys.argv[2]
subject = sys.argv[3]
body = sys.argv[4]

msg = MIMEText(body)
msg["Subject"] = subject
msg["From"] = sender
msg["To"] = rcpt

try:
    with smtplib.SMTP("127.0.0.1", 2525, timeout=15) as s:
        s.sendmail(sender, [rcpt], msg.as_string())
    print("SUBMIT_OK")
except Exception as ex:
    print(f"SUBMIT_ERROR: {ex}", file=sys.stderr)
    sys.exit(1)
'''


class CaptureServer:
    """Runs a disposable asyncio SMTP capture server on the Docker compose network."""

    def __init__(self, port=2525):
        self.name = f"gtest-capture-{secrets.token_hex(4)}"
        self.port = port

    def logs(self) -> str:
        res = subprocess.run(["docker", "logs", self.name], capture_output=True, text=True, timeout=30)
        return res.stdout + res.stderr

    def start(self):
        cmd = [
            "run", "-d", "--name", self.name, "--network", _network(),
            "--entrypoint", "python3", load_env_secret("IMAGE_NAME", "grommunio:local"),
            "-c", CAPTURE_STUB,
        ]
        res = _docker(*cmd)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to start capture server: {res.stderr}")
        if not wait_until(lambda: "READY: SMTP capture server listening" in self.logs(), 30, 0.5):
            self.cleanup()
            raise RuntimeError(f"Capture server did not start in time:\n{self.logs()[-500:]}")
        return self

    def stop(self):
        _docker("stop", self.name)

    def resume(self):
        _docker("start", self.name)
        if not wait_until(lambda: self.logs().count("READY: SMTP capture server listening") >= 2, 30, 0.5):
            raise RuntimeError(f"Capture server failed to resume:\n{self.logs()[-500:]}")

    def messages(self):
        msgs = []
        for line in self.logs().splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    msgs.append(json.loads(line))
                except Exception:
                    pass
        return msgs

    def cleanup(self):
        _docker("rm", "-fv", self.name)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.cleanup()


def check_outbound_relay():
    print("==> Checking Outbound Smarthost Relay & Queue Spool Retention...")
    require_harness()

    print("    [>] 1. Starting disposable SMTP capture server...")
    with CaptureServer() as capture:
        with ScratchDatabase() as db:
            env = {
                "RELAY_HOST": capture.name,
                "RELAY_PORT": str(capture.port),
            }
            print("    [>] 2. Starting throwaway Grommunio container configured with smarthost relay...")
            with Container(env=env, scratch=db) as c:
                if not c.wait_for_log("Starting Supervisor", 60):
                    print("[-] FAIL: Container failed to start Supervisor.", file=sys.stderr)
                    return False

                # Ensure Exim daemon is active
                if not wait_until(lambda: "RUNNING" in c.exec("supervisorctl", "status", "exim").stdout, 30, 1.0):
                    print("[-] FAIL: Exim failed to reach RUNNING state.", file=sys.stderr)
                    return False
                print("    [+] Exim service is RUNNING and connected to the capture relay.")

                # Phase 1: Test active relay delivery
                print("    [>] 3. Submitting outbound message to external recipient...")
                sender_a = "sender@example.com"
                rcpt_a = "someone@external.example"
                subject_a = f"RelayTest-A-{secrets.token_hex(4)}"
                body_a = "Automated test message for outbound relay verification."

                res = c.exec("python3", "-", sender_a, rcpt_a, subject_a, body_a, input=SUBMIT_SCRIPT, timeout=30)
                if res.returncode != 0 or "SUBMIT_OK" not in res.stdout:
                    print(f"[-] FAIL: In-container submission failed:\n{res.stderr or res.stdout}", file=sys.stderr)
                    return False

                # Assert delivery at capture server
                delivered_a = wait_until(lambda: any(subject_a in m.get("data", "") for m in capture.messages()), 30, 1.0)
                if not delivered_a:
                    print("[-] FAIL: Outbound message did not arrive at capture server.", file=sys.stderr)
                    return False

                msg_a = next(m for m in capture.messages() if subject_a in m.get("data", ""))
                if msg_a.get("from") != sender_a:
                    print(f"[-] FAIL: Expected envelope sender '{sender_a}', got '{msg_a.get('from')}'", file=sys.stderr)
                    return False
                if not any(rcpt_a in r for r in msg_a.get("rcpts", [])):
                    print(f"[-] FAIL: Expected recipient '{rcpt_a}' in {msg_a.get('rcpts')}", file=sys.stderr)
                    return False

                # Queue should be empty
                wait_until(lambda: c.exec("exim", "-bpc").stdout.strip() == "0", 15, 1.0)
                print("    [+] Outbound message successfully relayed to smarthost; spool is clean.")

                # Phase 2: Test spool queue retention during relay outage
                print("    [>] 4. Stopping capture server to simulate relay outage...")
                capture.stop()

                sender_b = "sender@example.com"
                rcpt_b = "someone-else@external.example"
                subject_b = f"RelayTest-B-{secrets.token_hex(4)}"
                body_b = "Message sent while smarthost relay is offline."

                res = c.exec("python3", "-", sender_b, rcpt_b, subject_b, body_b, input=SUBMIT_SCRIPT, timeout=30)
                if res.returncode != 0 or "SUBMIT_OK" not in res.stdout:
                    print(f"[-] FAIL: In-container submission during outage failed:\n{res.stderr or res.stdout}", file=sys.stderr)
                    return False

                # Message must remain in Exim's spool queue
                in_spool = wait_until(lambda: int(c.exec("exim", "-bpc").stdout.strip() or 0) >= 1, 15, 1.0)
                if not in_spool:
                    print("[-] FAIL: Message was dropped or bounced instead of being spooled in Exim queue.", file=sys.stderr)
                    return False

                queue_listing = c.exec("exim", "-bp").stdout
                if rcpt_b not in queue_listing:
                    print(f"[-] FAIL: Queued message not found in 'exim -bp':\n{queue_listing}", file=sys.stderr)
                    return False
                print("    [+] Message safely spooled in Exim queue during smarthost outage.")

                # Phase 3: Resume capture server and flush spool queue
                print("    [>] 5. Resuming capture server and forcing spool queue delivery (exim -qff)...")
                capture.resume()

                def check_and_flush():
                    if any(subject_b in m.get("data", "") for m in capture.messages()):
                        return True
                    c.exec("exim", "-qff")
                    return any(subject_b in m.get("data", "") for m in capture.messages())

                delivered_b = wait_until(check_and_flush, 30, 2.0)
                if not delivered_b:
                    print("[-] FAIL: Queued message was not delivered after capture server resumed.", file=sys.stderr)
                    return False

                # Spool queue must now be 0
                spool_clean = wait_until(lambda: c.exec("exim", "-bpc").stdout.strip() == "0", 15, 1.0)
                if not spool_clean:
                    print(f"[-] FAIL: Exim spool did not empty after flush (count: {c.exec('exim', '-bpc').stdout.strip()})", file=sys.stderr)
                    return False

                print("    [+] Queued message delivered upon smarthost recovery; spool cleanly emptied.")

    return True


def main():
    if not check_outbound_relay():
        print("[-] FAILED: Outbound smarthost relay verification failed.\n", file=sys.stderr)
        return 1
    print("[+] SUCCESS: Outbound smarthost relay & spool retention verified.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
