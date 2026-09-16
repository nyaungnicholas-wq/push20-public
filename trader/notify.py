"""Alerting and a structured run ledger for the scheduled rotation job.

WHY THIS EXISTS. The previous `_notify` shelled out to `osascript` -- macOS only
-- inside a bare `except Exception: pass`. On this Windows machine every alert
has therefore been a silent no-op since the port: not-connected, risk-gate
block, rebalance failure and zero-fill alike. The rotation missed five trading
days in August and nothing said a word.

THE GOVERNING RULE: an alert that cannot be delivered is ITSELF an error, never
a swallowed exception. `notify()` raises. A caller that cannot alert must not
continue quietly.

TWO TRAPS, both found by testing rather than reading:

  * A BurntToast-based toast looked correct and was not. With the module absent
    -- and it IS absent on this machine -- its `catch` block wrote "fallback"
    and PowerShell still exited 0, so the sink reported SUCCESS while showing
    nothing. The module built to make silence impossible was itself silent. So
    this uses the native WinRT toast API, which needs no module, and it demands
    the sentinel on stdout as well as exit 0: an exit code alone cannot tell
    "shown" from "caught the error and carried on".

  * Interpolating the title and message into a PowerShell command string is an
    injection. Alert text carries exception messages (`risk gate error: {e}`),
    so a quote or a `$(...)` in an exception would break or execute. Both are
    passed through the ENVIRONMENT instead, never through the command line.

THE LEDGER IS SEPARATE FROM ALERTING, on purpose. A run record is not an alert.
Conflating them is what made `skipped: market_closed`, `skipped: min_hold` and
**never fired** indistinguishable -- all three leave no REBALANCE line, so a day
the job never ran looked exactly like a day it correctly did nothing.
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
LEDGER_PATH = os.path.join(DATA, "rotation_runs.jsonl")
ALERT_LOG = os.path.join(DATA, "rotation_alerts.jsonl")


def _ledger_path():
    """Resolved at CALL time, not import time.

    The test suite imports this module and so wrote to the LIVE run ledger:
    measured 2026-08-31, four `ok:false reason:not_connected` rows appended by
    pytest runs, which then made verify-daily-rotation.ps1 alert about runs that
    never happened. Resolving here (rather than at import) means the override
    cannot be defeated by import order, which a module-level constant can.
    Reads the module global so the existing self-check, which swaps the globals,
    keeps working.
    """
    return os.environ.get("ROTATION_LEDGER_PATH") or LEDGER_PATH


def _alert_log_path():
    return os.environ.get("ROTATION_ALERT_LOG") or ALERT_LOG


WEBHOOK_ENV = "ROTATION_WEBHOOK_URL"

# The toast script prints this and nothing else on success. Exit code 0 is not
# sufficient evidence on its own -- a swallowed error also exits 0.
_TOAST_OK = "SHOWN"

_TOAST_PS = r"""
$ErrorActionPreference="Stop"
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$n=$t.GetElementsByTagName("text")
$n.Item(0).AppendChild($t.CreateTextNode($env:NT_TITLE)) | Out-Null
$n.Item(1).AppendChild($t.CreateTextNode($env:NT_MSG)) | Out-Null
$toast=[Windows.UI.Notifications.ToastNotification]::new($t)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Rotation").Show($toast)
Write-Output "SHOWN"
"""


class NotifyError(Exception):
    """A CONFIGURED sink failed to deliver.

    An UNCONFIGURED sink is skipped, which is not an error -- there is nothing
    to fail. A configured one that fails is, and it stops the caller.
    """


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path, record):
    """Append one record and fsync it.

    OSError is deliberately allowed to propagate. A ledger write that fails
    quietly is precisely the failure mode this module exists to remove.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return record


def toast(title, message):
    """Native Windows toast. True only if it actually displayed. Never raises.

    Title and message travel in the environment, not in the command string --
    alert text contains exception messages, and a quote or `$(...)` in one
    would otherwise break or execute.
    """
    env = dict(os.environ, NT_TITLE=str(title), NT_MSG=str(message))
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS],
            capture_output=True, text=True, timeout=20, env=env)
    except (OSError, subprocess.SubprocessError):
        print(f"TOAST FAILED: {title} - {message}", file=sys.stderr)
        return False
    # Both conditions. Exit 0 alone would pass for a script that caught its own
    # error and carried on -- which is exactly how the first version lied.
    return done.returncode == 0 and _TOAST_OK in (done.stdout or "")


# ntfy renders the POST body as the notification text and takes the title from a
# header. Sending it our generic JSON envelope instead would deliver a wall of
# raw JSON to the phone: technically a successful delivery, practically unread.
_NTFY_PRIORITY = {"alert": "high", "warn": "default", "info": "low"}

# Discord embed stripe colour per severity, as the decimal integers the API wants.
# Discord REJECTS a payload with neither `content` nor `embeds`, so the generic
# JSON envelope below would 400 on every send.
_DISCORD_COLOUR = {"alert": 15026253, "warn": 14262593, "info": 5031889}


def webhook(title, message, level):
    """POST the alert. Returns "skipped" when no URL is configured.

    An unconfigured channel is not a failure; it has nothing to deliver.

    Three payload shapes, chosen by URL:

    ntfy.sh    plain-text body plus ntfy's own headers, so the alert is legible
               on a phone rather than a wall of raw JSON.
    Discord    one embed. Discord REJECTS any payload carrying neither `content`
               nor `embeds`, so the generic envelope below would 400 on every
               send. Success is 204 No Content, not 200.
    anything   the generic JSON envelope a Slack or custom endpoint expects.
    """
    url = os.environ.get(WEBHOOK_ENV)
    if not url:
        return "skipped"
    if "ntfy.sh/" in url:
        data = message.encode("utf-8")
        # Emoji and em-dashes are common in these titles and ntfy sends headers
        # as latin-1, so they must go. Collapse the whitespace they leave behind
        # or the phone shows "PUSH-20 traded  5 orders" with a hole in it.
        clean_title = " ".join(title.encode("ascii", "ignore").decode().split())
        if not clean_title:
            clean_title = "PUSH-20"
        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "Title": clean_title,
            "Priority": _NTFY_PRIORITY.get(level, "default"),
            "Tags": "chart_with_upwards_trend",
        }
    elif any(s in url for s in ("discord.com/api/webhooks", "discordapp.com/api/webhooks")):
        def _truncate(s, limit):
            s = str(s)
            if len(s) <= limit:
                return s
            if limit <= 3:
                return s[:limit]
            return s[:limit - 3] + "..."
        desc = message.strip()
        if not desc:
            desc = "-"
        data = json.dumps({"embeds": [{
            "title": _truncate(title, 256),
            "description": _truncate(desc, 4096),
            "color": _DISCORD_COLOUR.get(level, _DISCORD_COLOUR["info"]),
            "timestamp": _utc()
        }]}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
    else:
        data = json.dumps({"title": title, "message": message,
                           "level": level, "utc": _utc()}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.getcode() < 300
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        # OSError covers socket timeouts and TLS failures, which are not
        # URLError subclasses in every path and would otherwise escape.
        return False


def notify(title, message, level="info"):
    """Deliver an alert, record the attempt, and RAISE if a sink failed.

    Raising is the point. Every caller in rotation_live reaches _notify on a
    path where something has already gone wrong; continuing past a failed
    delivery is how the last five missed days went unreported.

    The record is appended BEFORE raising, so a failed delivery is still
    evidence rather than a gap.
    """
    results = {"toast": toast(title, message),
               "webhook": webhook(title, message, level)}
    record = {"utc": _utc(), "level": level, "title": title,
              "message": message, "results": results}
    _append_jsonl(_alert_log_path(), record)
    print(f"ALERT: {title} - {message}", file=sys.stderr)
    failed = sorted(k for k, v in results.items() if v is False)
    if failed:
        raise NotifyError(
            f"alert delivery failed on {', '.join(failed)} "
            f"(title={title!r}). The alert was recorded but nobody was told.")
    return record


def ledger(action, reason="", session_et="", target_session="",
           days_held=None, orders_sent=0, ok=True, **extra):
    """Record one run outcome.

    Called on EVERY path, including every early return and the risk-gate
    `blocked` branch. A path with no ledger row is indistinguishable from a run
    that never happened, which is the ambiguity that hid the August gap.
    """
    record = {"utc": _utc(), "session_et": session_et,
              "target_session": target_session, "action": action,
              "reason": reason, "days_held": days_held,
              "orders_sent": orders_sent, "ok": ok}
    record.update(extra)
    return _append_jsonl(_ledger_path(), record)


def read_ledger(path=None, limit=None):
    """Records oldest-first. A missing file is empty, not an error."""
    path = path or _ledger_path()
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out[-limit:] if limit else out


def demo():
    import tempfile

    g = globals()
    saved = {k: g[k] for k in ("LEDGER_PATH", "ALERT_LOG", "toast")}
    saved_env = os.environ.pop(WEBHOOK_ENV, None)
    try:
        with tempfile.TemporaryDirectory() as td:
            g["LEDGER_PATH"] = os.path.join(td, "runs.jsonl")
            g["ALERT_LOG"] = os.path.join(td, "alerts.jsonl")

            # --- ledger ---
            assert read_ledger(os.path.join(td, "absent.jsonl")) == []
            ledger("rebalance", reason="", days_held=3, orders_sent=5)
            rows = read_ledger()
            assert len(rows) == 1 and rows[0]["action"] == "rebalance"
            assert rows[0]["orders_sent"] == 5

            ledger("skip", reason="market_closed")
            ledger("skip", reason="min_hold", extra_field="kept")
            rows = read_ledger()
            assert len(rows) == 3
            assert rows[-1]["extra_field"] == "kept", "**extra must reach the row"
            assert [r["reason"] for r in read_ledger(limit=2)] == \
                ["market_closed", "min_hold"], "limit must take the LAST n"

            # the three states that used to be one silence are now distinct
            assert {r["reason"] for r in rows} == {"", "market_closed", "min_hold"}

            # --- webhook unconfigured is skipped, not failed ---
            assert webhook("t", "m", "info") == "skipped"

            # --- notify: succeeds, and does not raise on a skipped sink ---
            g["toast"] = lambda t, m: True
            rec = notify("ok title", "ok message")
            assert rec["results"] == {"toast": True, "webhook": "skipped"}

            # --- notify: a configured sink that fails must RAISE ---
            g["toast"] = lambda t, m: False
            try:
                notify("bad title", "bad message")
            except NotifyError as e:
                assert "toast" in str(e)
            else:
                raise AssertionError("notify must raise when a sink fails")

            # ...and the attempt must still have been recorded
            alerts = read_ledger(g["ALERT_LOG"])
            assert len(alerts) == 2, "a failed delivery must still be logged"
            assert alerts[-1]["results"]["toast"] is False
    finally:
        g.update(saved)
        if saved_env is not None:
            os.environ[WEBHOOK_ENV] = saved_env

    # --- the real toast, unmocked. This is the check the first version failed:
    # BurntToast is absent here, and that implementation still returned True.
    assert toast("rotation selftest", "if you can see this, the sink works"), \
        "the real toast sink did not confirm delivery"


if __name__ == "__main__":
    demo()
    print("OK")
