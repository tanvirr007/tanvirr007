import os
import sys
import json
import time
import signal
import subprocess
import re
import urllib.request
import urllib.parse
from pathlib import Path

STATE_FILE = Path(".tg_state.json")
STATUS_FILE = Path(".tg_status")
STOP_FILE = Path(".tg_stop")
PID_FILE = Path(".tg_pid")


def escape_html(text: str) -> str:
    if not text:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_time(seconds: float) -> str:
    total = int(max(0, seconds))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h:02d}h:{m:02d}m:{s:02d}s"
    return f"{m:02d}m:{s:02d}s"


def format_trigger(event_name: str) -> str:
    triggers = {
        "schedule": "Daily schedule",
        "workflow_dispatch": "Manual run",
        "push": "Push to main",
    }
    return triggers.get(event_name, event_name)


def send_request(req: urllib.request.Request) -> bytes:
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.read()
    except Exception as e:
        if hasattr(e, "read"):
            try:
                err_body = e.read().decode("utf-8")
                print(f"Telegram API Error: {err_body}", file=sys.stderr)
            except Exception:
                pass
        raise e


def send_telegram_message(token: str, chat_id: str, message: str, reply_markup: dict = None) -> int:
    data = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        data["reply_markup"] = reply_markup

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    res_bytes = send_request(req)
    res = json.loads(res_bytes)
    return res.get("result", {}).get("message_id")


def edit_telegram_message(token: str, chat_id: str, message_id: int, message: str, reply_markup: dict = None):
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        data["reply_markup"] = reply_markup

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/editMessageText",
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    send_request(req)


def get_common_context():
    full_repo = os.environ.get("GITHUB_REPOSITORY", "tanvirr007/tanvirr007")
    repo = full_repo.split("/")[-1] if "/" in full_repo else full_repo
    raw_event = os.environ.get("GITHUB_EVENT_NAME", "schedule")
    trigger = format_trigger(raw_event)
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_url = f"{server}/{full_repo}/actions/runs/{run_id}" if run_id else f"{server}/{full_repo}/actions"
    repo_url = f"{server}/{full_repo}"
    return {
        "repo": repo,
        "trigger": trigger,
        "run_url": run_url,
        "repo_url": repo_url
    }


def cleanup_state():
    for f in [STATE_FILE, STATUS_FILE, STOP_FILE, PID_FILE]:
        try:
            if f.exists():
                f.unlink()
        except Exception:
            pass


def extract_single_metric(line: str):
    m_f = re.search(r"Followed by ([\d\.,]+) users", line, re.I)
    if m_f:
        return "Followers", m_f.group(1)

    m = re.match(r"^([\d\.,kK]+)\s+(.+)$", line)
    if m:
        val, label = m.group(1), m.group(2)
        low = label.lower()
        if "commit" in low:
            return "Commits", val
        if "stargazer" in low or "star" in low:
            return "Stargazers", val
        if "repositor" in low:
            return "Repositories", val
        if "views in last" in low:
            return "Views (2 weeks)", val
    return None, None


def get_svg_metrics_diff():
    diff_text = ""
    try:
        proc = subprocess.run(["git", "diff", "HEAD", "--", "github-metrics.svg"], capture_output=True, text=True)
        if proc.stdout.strip():
            diff_text = proc.stdout
    except Exception:
        pass

    if not diff_text.strip():
        try:
            proc = subprocess.run(["git", "diff", "HEAD~1", "HEAD", "--", "github-metrics.svg"], capture_output=True, text=True)
            if proc.stdout.strip():
                diff_text = proc.stdout
        except Exception:
            pass

    if not diff_text:
        return []

    removed_lines, added_lines = [], []
    has_calendar = False

    for line in diff_text.splitlines():
        if line.startswith(("---", "+++", "@@")):
            continue
        if "Last updated" in line:
            continue
        if "rect class=" in line and "day" in line:
            has_calendar = True
            continue

        stripped = line[1:].strip()
        if not stripped:
            continue

        if line.startswith("-"):
            removed_lines.append(stripped)
        elif line.startswith("+"):
            added_lines.append(stripped)

    removed_metrics = {}
    for r in removed_lines:
        key, val = extract_single_metric(r)
        if key:
            removed_metrics[key] = val

    changes = {}
    for a in added_lines:
        key, new_val = extract_single_metric(a)
        if key and key in removed_metrics:
            old_val = removed_metrics[key]
            if old_val != new_val:
                try:
                    diff_num = int(new_val.replace(",", "")) - int(old_val.replace(",", ""))
                    sign = "+" if diff_num > 0 else ""
                    changes[key] = f"• {key}: {old_val} → {new_val} ({sign}{diff_num})"
                except ValueError:
                    changes[key] = f"• {key}: {old_val} → {new_val}"

    order = ["Commits", "Stargazers", "Followers", "Repositories", "Views (2 weeks)"]
    result = [changes[k] for k in order if k in changes]

    if has_calendar and not any("calendar" in c.lower() for c in result):
        result.append("• Activity calendar updated")

    return result


def daemon_loop(token: str, chat_id: str, message_id: int, start_time: float):
    ctx = get_common_context()
    markup = {
        "inline_keyboard": [
            [
                {"text": "View Workflow Run", "url": ctx["run_url"]}
            ]
        ]
    }
    last_text = ""

    while True:
        time.sleep(3)
        if STOP_FILE.exists():
            break

        current_status = "In progress..."
        if STATUS_FILE.exists():
            try:
                content = STATUS_FILE.read_text(encoding="utf-8").strip()
                if content:
                    current_status = content
            except Exception:
                pass

        elapsed = time.time() - start_time
        text = (
            f"<b>Profile Readme Update</b>\n\n"
            f"• Repository: <code>{escape_html(ctx['repo'])}</code>\n"
            f"• Trigger: <code>{escape_html(ctx['trigger'])}</code>\n"
            f"• Elapsed: <code>{format_time(elapsed)}</code>\n\n"
            f"<blockquote>{escape_html(current_status)}</blockquote>"
        )

        if text != last_text:
            try:
                edit_telegram_message(token, chat_id, message_id, text, reply_markup=markup)
                last_text = text
            except Exception:
                pass


def cmd_start(token: str, chat_id: str):
    cleanup_state()
    start_time = time.time()
    ctx = get_common_context()

    initial_status = "Starting workflow..."
    STATUS_FILE.write_text(initial_status, encoding="utf-8")

    text = (
        f"<b>Profile Readme Update</b>\n\n"
        f"• Repository: <code>{escape_html(ctx['repo'])}</code>\n"
        f"• Trigger: <code>{escape_html(ctx['trigger'])}</code>\n"
        f"• Elapsed: <code>00m:00s</code>\n\n"
        f"<blockquote>{escape_html(initial_status)}</blockquote>"
    )

    markup = {
        "inline_keyboard": [
            [
                {"text": "View Workflow Run", "url": ctx["run_url"]}
            ]
        ]
    }

    try:
        msg_id = send_telegram_message(token, chat_id, text, reply_markup=markup)
        if not msg_id:
            print("Warning: Could not get message_id from Telegram.")
            return

        state = {"message_id": msg_id, "start_time": start_time}
        STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
        print(f"Telegram: initial message sent ({msg_id})")

        # Fork background daemon on Linux
        if hasattr(os, "fork"):
            pid = os.fork()
            if pid > 0:
                PID_FILE.write_text(str(pid), encoding="utf-8")
                print(f"Telegram: live background updater running (PID {pid})")
                sys.exit(0)

            # Child process: detach stdio
            sys.stdout.flush()
            sys.stderr.flush()
            with open(os.devnull, "r") as devnull_r, open(os.devnull, "w") as devnull_w:
                os.dup2(devnull_r.fileno(), sys.stdin.fileno())
                os.dup2(devnull_w.fileno(), sys.stdout.fileno())
                os.dup2(devnull_w.fileno(), sys.stderr.fileno())

            daemon_loop(token, chat_id, msg_id, start_time)
            sys.exit(0)
        else:
            # Fallback for environments without os.fork
            pass

    except Exception as e:
        print(f"Warning: Failed to start live updater: {e}", file=sys.stderr)


def cmd_step(step_name: str):
    try:
        STATUS_FILE.write_text(step_name, encoding="utf-8")
        print(f"Telegram status updated: {step_name}")
    except Exception as e:
        print(f"Warning: Could not update status: {e}", file=sys.stderr)


def stop_daemon():
    STOP_FILE.write_text("stop", encoding="utf-8")
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
    time.sleep(1)


def cmd_finish_success(token: str, chat_id: str):
    stop_daemon()

    msg_id = None
    start_time = time.time()
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            msg_id = data.get("message_id")
            start_time = data.get("start_time", start_time)
        except Exception:
            pass

    duration = time.time() - start_time
    ctx = get_common_context()

    change_id = os.environ.get("CHANGE_ID", "")
    commit_sha = os.environ.get("GITHUB_SHA", "")[:7]
    has_changes = os.environ.get("HAS_CHANGES", "true").lower() in ("true", "1", "yes")

    status_desc = "Changes committed" if has_changes else "Already up to date"
    cid_line = f"• Change-Id: <code>{escape_html(change_id)}</code>\n" if change_id else ""
    commit_line = f"• Commit: <code>{escape_html(commit_sha)}</code>\n" if commit_sha else ""

    # Parse SVG metrics changes
    diff_items = get_svg_metrics_diff()
    if diff_items:
        details_quote = "Metrics updated:\n" + "\n".join(diff_items)
    elif has_changes:
        details_quote = "Profile updated"
    else:
        details_quote = "No metric changes (already up to date)."

    text = (
        f"<b>Profile Readme Updated</b>\n\n"
        f"• Repository: <code>{escape_html(ctx['repo'])}</code>\n"
        f"• Trigger: <code>{escape_html(ctx['trigger'])}</code>\n"
        f"• Status: <code>{status_desc}</code>\n"
        f"{commit_line}"
        f"{cid_line}"
        f"• Duration: <code>{format_time(duration)}</code>\n\n"
        f"<blockquote>{escape_html(details_quote)}</blockquote>"
    )

    markup = {
        "inline_keyboard": [
            [
                {"text": "View Profile", "url": ctx["repo_url"]},
                {"text": "View Workflow Run", "url": ctx["run_url"]}
            ]
        ]
    }

    try:
        if msg_id:
            edit_telegram_message(token, chat_id, msg_id, text, reply_markup=markup)
        else:
            send_telegram_message(token, chat_id, text, reply_markup=markup)
        print(f"Telegram: completion message sent ({format_time(duration)})")
    except Exception as e:
        print(f"Warning: Failed to send success message: {e}", file=sys.stderr)
    finally:
        cleanup_state()


def cmd_finish_fail(token: str, chat_id: str, reason: str = None):
    stop_daemon()

    msg_id = None
    start_time = time.time()
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            msg_id = data.get("message_id")
            start_time = data.get("start_time", start_time)
        except Exception:
            pass

    duration = time.time() - start_time
    ctx = get_common_context()
    error_detail = reason or "Workflow run failed."

    text = (
        f"<b>Profile Readme Update Failed</b>\n\n"
        f"• Repository: <code>{escape_html(ctx['repo'])}</code>\n"
        f"• Trigger: <code>{escape_html(ctx['trigger'])}</code>\n"
        f"• Duration: <code>{format_time(duration)}</code>\n\n"
        f"<blockquote>{escape_html(error_detail)}</blockquote>"
    )

    markup = {
        "inline_keyboard": [
            [
                {"text": "View Run Logs", "url": ctx["run_url"]}
            ]
        ]
    }

    try:
        if msg_id:
            edit_telegram_message(token, chat_id, msg_id, text, reply_markup=markup)
        else:
            send_telegram_message(token, chat_id, text, reply_markup=markup)
        print(f"Telegram: failure alert sent ({format_time(duration)})")
    except Exception as e:
        print(f"Warning: Failed to send failure message: {e}", file=sys.stderr)
    finally:
        cleanup_state()


def main():
    if len(sys.argv) < 2:
        print("Usage: notify.py [start | step <text> | finish success | finish fail [reason]]")
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "step":
        step_text = sys.argv[2] if len(sys.argv) > 2 else "In progress..."
        cmd_step(step_text)
        sys.exit(0)

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Notice: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured. Skipping.")
        sys.exit(0)

    if cmd == "start":
        cmd_start(token, chat_id)
    elif cmd == "finish":
        sub = sys.argv[2].lower() if len(sys.argv) > 2 else "success"
        if sub == "success":
            cmd_finish_success(token, chat_id)
        else:
            reason = sys.argv[3] if len(sys.argv) > 3 else "Workflow run failed."
            cmd_finish_fail(token, chat_id, reason)
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
