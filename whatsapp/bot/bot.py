#!/usr/bin/env python3
"""
claude-whatsapp-bot  (v2, windowed history + per-message routing)
==================================================================
Watches a self-WhatsApp group ("My Claude") via the whatsapp-mcp bridge's local
SQLite DB and runs each incoming message through the `claude` CLI, posting the
reply back through the bridge's HTTP /api/send endpoint.

What changed from v1 (the single ever-growing --resume session):
  • No giant session. Every message gets a FRESH claude run seeded with a bounded
    window of recent group history (default 25 msgs, via `wa-history`). The model
    can run `wa-history --limit N` itself to look deeper. This stops the token
    burn of replaying the whole transcript every turn.
  • Per-message routing by a Sonnet judge: when something is already in flight,
    each new message is classified as a FOLLOWUP (continues the current thread →
    queued serially behind it) or NEW (independent → its own claude, runs
    concurrently). When nothing is in flight, it just runs, no judge call.
  • The bot logs BOTH sides to ~/.config/claude-whatsapp/history.jsonl (the
    bridge never stores the bot's own replies), which is what `wa-history` reads.

Loop-avoidance: replies are prefixed with REPLY_MARKER and ignored on the way
back; messages are tracked by monotonic sqlite rowid so nothing is processed
twice.
"""

import contextlib
import datetime
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import sqlite3

# ----------------------------------------------------------------------------
# Config (env vars override the defaults)
# ----------------------------------------------------------------------------
HOME          = os.path.expanduser("~")
GROUP_NAME    = os.environ.get("CWB_GROUP_NAME", "My Claude")
MESSAGES_DB   = os.environ.get("CWB_MESSAGES_DB",
                    os.path.join(os.environ.get("WA_REPO", os.path.join(HOME, "code/whatsapp-mcp")), "whatsapp-bridge/store/messages.db"))
BRIDGE_SEND   = os.environ.get("CWB_BRIDGE_SEND", "http://localhost:8080/api/send")
BRIDGE_REACT  = os.environ.get("CWB_BRIDGE_REACT", "http://localhost:8080/api/react")
CLAUDE_BIN    = os.environ.get("CWB_CLAUDE_BIN", os.path.join(HOME, ".local/bin/claude"))
CLAUDE_CWD    = os.environ.get("CWB_CLAUDE_CWD", HOME)
STATE_FILE    = os.environ.get("CWB_STATE_FILE",
                    os.path.join(HOME, ".config/claude-whatsapp/state.json"))
HISTORY_FILE  = os.environ.get("CWB_HISTORY_FILE",
                    os.path.join(HOME, ".config/claude-whatsapp/history.jsonl"))
HISTORY_CMD   = os.environ.get("CWB_HISTORY_CMD", os.path.join(HOME, ".local/bin/wa-history"))
# Proof-of-life for the watchdog. Touched once per successful poll, so its mtime
# answers the question no process-level check can: not "is the bot running" but
# "is the bot still reading the group". Every time this bot has been broken it
# was alive and deaf, and `systemctl is-active` said healthy throughout.
HEARTBEAT_FILE = os.environ.get("CWB_HEARTBEAT_FILE",
                    os.path.join(HOME, ".config/claude-whatsapp/heartbeat"))
POLL_INTERVAL = float(os.environ.get("CWB_POLL_INTERVAL", "3"))
# Consecutive db-poll failures tolerated before we exit and let systemd restart
# us. ~1 minute at the default 3s poll: long enough to ride out a bridge
# rebuild, short enough that the group is never deaf for hours.
MAX_POLL_FAILURES = int(os.environ.get("CWB_MAX_POLL_FAILURES", "20"))
# Per-message cap, raised from v1's 600s so long jobs (builds, big refactors)
# aren't guillotined mid-reply.
# No timeout on the work run. A real job (an APK build, a long dig through a
# repo) can outrun any deadline worth picking, and killing it at the buzzer
# throws away everything it had finished. subprocess.run(timeout=None) waits.
# Set CWB_CLAUDE_TIMEOUT to a positive number of seconds to reinstate a cap.
_claude_timeout  = os.environ.get("CWB_CLAUDE_TIMEOUT", "0").strip()
CLAUDE_TIMEOUT   = int(_claude_timeout) if _claude_timeout.isdigit() and \
                   int(_claude_timeout) > 0 else None
CLASSIFY_TIMEOUT = int(os.environ.get("CWB_CLASSIFY_TIMEOUT", "90"))
# A run that dies on an API error (output content filter, overload, a 5xx) used
# to lose the message outright: the user got a JSON dump and a ❌, and nobody
# ever did the work. Retry it instead, backing off, and rebuild the prompt each
# time so the retry isn't a byte-identical rerun of what just failed.
CLAUDE_RETRIES    = int(os.environ.get("CWB_CLAUDE_RETRIES", "3"))
CLAUDE_RETRY_WAIT = float(os.environ.get("CWB_CLAUDE_RETRY_WAIT", "20"))
MAX_CHUNK     = int(os.environ.get("CWB_MAX_CHUNK", "4000"))
REPLY_MARKER  = os.environ.get("CWB_REPLY_MARKER", "🤖")
REPLY_HEADER  = REPLY_MARKER * 3

# Other tools on this machine may post NOTICES into the same self-group (a cron
# job saying "backup done", a build bot asking for approval). Give each such tool
# its own emoji prefix and list it here: those messages are addressed to you, not
# to this bot, and answering each one turns a notification into a conversation
# nobody asked for. Comma separated, empty by default.
IGNORE_PREFIXES = tuple(x for x in os.environ.get(
    "CWB_IGNORE_PREFIXES", "").split(",") if x)

# A run that answers with exactly this marker sends nothing to the chat: it
# handed the message to someone who answers there themselves (another bot),
# and a second voice on top of that is spam. The message still gets its ✅.
SILENT_MARKER = os.environ.get("CWB_SILENT_MARKER", "[[SILENT]]")

# Reactions on the user's own message act as progress receipts: 👀 the moment the
# message is picked up, then ✅ once its reply has been sent (❌ if the run
# failed). WhatsApp keeps only one reaction per sender per message, so each new
# one replaces the previous instead of piling up.
ACK_EMOJI     = os.environ.get("CWB_ACK_EMOJI", "👀")
DONE_EMOJI    = os.environ.get("CWB_DONE_EMOJI", "✅")
ERROR_EMOJI   = os.environ.get("CWB_ERROR_EMOJI", "❌")
REACTIONS_ON  = os.environ.get("CWB_REACTIONS", "1").lower() not in ("0", "false", "no")

# How much history each run sees by default, and how many parallel independent
# tasks may run at once (the serial "followup" worker is separate from this cap).
HISTORY_LIMIT = int(os.environ.get("CWB_HISTORY_LIMIT", "25"))
MAX_PARALLEL  = int(os.environ.get("CWB_MAX_PARALLEL", "3"))

# Messages that pile up behind a long job are answered together by one run
# instead of one stale run each, and a run is told when a message it is
# answering is old rather than being left to imply "just now".
COALESCE_MAX  = int(os.environ.get("CWB_COALESCE_MAX", "8"))
STALE_AFTER   = int(os.environ.get("CWB_STALE_AFTER", "180"))

# Replay guard. The bridge stores messages with INSERT OR REPLACE, and SQLite
# implements REPLACE as delete+insert, so a re-synced message gets a *new*
# rowid and sails past the last_rowid watermark. After a re-link WhatsApp sends
# a full history sync, which without this guard means the bot wakes up and
# answers days of old messages at once. Two independent defences:
#   * seen ids: never answer a message id twice, whatever its rowid;
#   * max age: never answer anything older than this (it is history, not a
#     question). Genuine late replies (queued behind a long job) are
#     minutes-to-hours old and still get through.
SEEN_IDS_KEEP   = int(os.environ.get("CWB_SEEN_IDS_KEEP", "800"))
REPLAY_MAX_AGE  = int(os.environ.get("CWB_REPLAY_MAX_AGE", str(6 * 3600)))

# Model used only for the routing judge (cheap, one word). Workers use the
# default model unless CWB_WORK_MODEL is set.
CLASSIFY_MODEL = os.environ.get("CWB_CLASSIFY_MODEL", "sonnet")
WORK_MODEL     = os.environ.get("CWB_WORK_MODEL", "")

# Thinking level. Each message is scanned for a level the user asked for
# ("xhigh", "ultrathink", "low effort"); when they didn't say, use the default.
EFFORT_LEVELS  = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT = os.environ.get("CWB_DEFAULT_EFFORT", "high")
EFFORT_MODEL   = os.environ.get("CWB_EFFORT_MODEL", "sonnet")
EFFORT_TIMEOUT = int(os.environ.get("CWB_EFFORT_TIMEOUT", "60"))

PERMISSION_ARGS = os.environ.get(
    "CWB_PERMISSION_ARGS", "--dangerously-skip-permissions").split()

SYSTEM_PROMPT = os.environ.get("CWB_SYSTEM_PROMPT",
    "You are replying to the user over WhatsApp on their own Linux PC. "
    "Keep answers concise and phone-friendly: short paragraphs, no big markdown "
    "tables, plain text. The user can see your reply on their phone. "
    "You are given a window of recent chat history for context; if you need more, "
    "run `wa-history --limit N` (or `--before <timestamp>` to page further back).")


def log(*a):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}]", *a, flush=True)


def beat():
    """Record that the poll loop just completed a cycle. Never raises: a
    watchdog signal that can take the bot down is worse than no signal."""
    try:
        os.makedirs(os.path.dirname(HEARTBEAT_FILE), exist_ok=True)
        with open(HEARTBEAT_FILE, "a"):
            os.utime(HEARTBEAT_FILE, None)
    except Exception:
        pass


def now_ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------------
# State
# ----------------------------------------------------------------------------
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        log("WARN could not read state, starting fresh:", e)
        return {}


_state_lock = threading.Lock()


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with _state_lock:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)


# ----------------------------------------------------------------------------
# Transcript (both sides), the bridge only stores the user's messages, so we
# keep our own log here for `wa-history` to read.
# ----------------------------------------------------------------------------
_hist_lock = threading.Lock()


def append_history(role, text, ids=None):
    """`ids` are the WhatsApp message IDs this line was actually sent as (a long
    reply is several). They are what lets anything downstream react to, quote or
    point at a specific message; without them a logged line is anonymous."""
    text = (text or "").strip()
    if not text:
        return
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    rec = {"ts": now_ts(), "role": role, "text": text}
    ids = [i for i in (ids or []) if i]
    if ids:
        rec["ids"] = ids
    line = json.dumps(rec, ensure_ascii=False)
    with _hist_lock:
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def seed_history_from_bridge(group_jid, limit=50):
    """One-time backfill of the user's side so cutover keeps context."""
    if os.path.exists(HISTORY_FILE):
        return
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT content, timestamp FROM messages WHERE chat_jid = ? "
                "AND COALESCE(content,'') != '' ORDER BY rowid DESC LIMIT ?",
                (group_jid, limit)).fetchall()
    except Exception as e:
        log("WARN history seed failed:", e)
        return
    rows.reverse()
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with _hist_lock, open(HISTORY_FILE, "a", encoding="utf-8") as f:
        for content, ts in rows:
            content = (content or "").strip()
            if content.startswith(REPLY_MARKER):
                continue
            f.write(json.dumps({"ts": str(ts)[:19], "role": "user",
                                "text": content}, ensure_ascii=False) + "\n")
    log("Seeded history from", len(rows), "bridge messages")


def history_text(limit=HISTORY_LIMIT):
    try:
        out = subprocess.run([HISTORY_CMD, "--limit", str(limit)],
                             capture_output=True, text=True, timeout=20)
        return out.stdout.strip()
    except Exception as e:
        log("WARN history_text failed:", e)
        return "(history unavailable)"


# ----------------------------------------------------------------------------
# DB access (read-only)
# ----------------------------------------------------------------------------
@contextlib.contextmanager
def db():
    # `with sqlite3.connect(...)` only wraps a transaction; it never closes the
    # connection. The 3s poll then leaked an fd per tick until the 1024 limit,
    # where every connect fails with "unable to open database file" and the bot
    # goes silently deaf. Close it here so no caller has to remember.
    conn = sqlite3.connect(f"file:{MESSAGES_DB}?mode=ro", uri=True, timeout=10)
    try:
        yield conn
    finally:
        conn.close()


def resolve_group_jid(conn):
    cur = conn.execute(
        "SELECT jid FROM chats WHERE name = ? AND jid LIKE '%@g.us' "
        "ORDER BY last_message_time DESC LIMIT 1", (GROUP_NAME,))
    row = cur.fetchone()
    return row[0] if row else None


def max_rowid(conn):
    return conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM messages").fetchone()[0]


def new_messages(conn, group_jid, after_rowid):
    cur = conn.execute(
        "SELECT rowid, id, content, COALESCE(media_type,''), COALESCE(sender,''), "
        "COALESCE(timestamp,'') "
        "FROM messages WHERE chat_jid = ? AND rowid > ? "
        "ORDER BY rowid ASC", (group_jid, after_rowid))
    return cur.fetchall()


# ----------------------------------------------------------------------------
# Sending
# ----------------------------------------------------------------------------
_send_lock = threading.Lock()


def send(group_jid, text, quote=None):
    # store_local: ask the bridge to write this into messages.db too. WhatsApp
    # does not echo a message back to the device that sent it, so without this
    # our own replies exist only on the phone: wa-history shows a one-sided
    # conversation, and restart-after-reply.sh waits an hour for a reply row
    # that never arrives. Safe to store because every chunk is prefixed with
    # REPLY_MARKER, which the poll loop skips.
    body = {"recipient": group_jid, "message": text, "store_local": True}
    if quote and quote.get("id"):
        body["quoted_id"] = quote["id"]
        body["quoted_sender"] = quote.get("sender", "")
        body["quoted_text"] = (quote.get("text", "") or "")[:700]
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        BRIDGE_SEND, data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode() or "{}")
            if not body.get("success", False):
                log("WARN send not successful:", body.get("message"))
                return ""
            # Newer bridges hand back the ID of the message they just sent;
            # an older one returns nothing, and callers treat "" as "sent, but
            # I can't point at it".
            return body.get("id", "") or True
    except urllib.error.URLError as e:
        log("ERROR send failed (bridge down?):", e)
        return ""
    except Exception as e:
        log("ERROR send exception:", e)
        return ""


def react(group_jid, msg_id, emoji):
    # Best-effort by design: a reaction is only a progress hint, so a bridge that
    # is down or an older bridge without /api/react must never take down the
    # reply it decorates. Failures are logged and swallowed.
    if not REACTIONS_ON or not msg_id:
        return False
    payload = json.dumps({"chat_jid": group_jid, "message_id": msg_id,
                          "emoji": emoji}).encode()
    req = urllib.request.Request(
        BRIDGE_REACT, data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode() or "{}")
            if not body.get("success", False):
                log("WARN react not successful:", body.get("message"))
                return False
            record_reaction(msg_id, "claude", emoji)
            return True
    except Exception as e:
        log("WARN react failed:", e)
        return False


# Reactions are mirrored to a small JSON file so a custom phone UI could render both sides (optional).
# Keyed by WhatsApp message ID because that is the only name both halves know.
REACTIONS_FILE = os.path.join(HOME, ".config/claude-whatsapp/reactions.json")
_react_file_lock = threading.Lock()


def record_reaction(msg_id, who, emoji):
    """Best-effort mirror of a reaction we just sent. Never worth an exception:
    a missing entry only costs a missing emoji in the app."""
    try:
        with _react_file_lock:
            try:
                with open(REACTIONS_FILE, encoding="utf-8") as f:
                    d = json.load(f)
                if not isinstance(d, dict):
                    d = {}
            except (FileNotFoundError, ValueError):
                d = {}
            entry = d.get(msg_id) or {}
            if emoji:
                entry[who] = emoji
            else:
                entry.pop(who, None)
            if entry:
                d[msg_id] = entry
            else:
                d.pop(msg_id, None)
            os.makedirs(os.path.dirname(REACTIONS_FILE), exist_ok=True)
            tmp = REACTIONS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
            os.replace(tmp, REACTIONS_FILE)
    except Exception as e:
        log("WARN reaction mirror failed:", e)


def chunks(text, n=MAX_CHUNK):
    text = text.rstrip()
    if len(text) <= n:
        return [text]
    out, buf = [], ""
    for line in text.split("\n"):
        while len(line) > n:
            if buf:
                out.append(buf); buf = ""
            out.append(line[:n]); line = line[n:]
        if len(buf) + len(line) + 1 > n:
            out.append(buf); buf = line
        else:
            buf = buf + "\n" + line if buf else line
    if buf:
        out.append(buf)
    return out


def reply(group_jid, text, tag="", quote=None):
    # Serialize sends so parallel workers don't interleave a single multi-part
    # reply. `tag` labels a reply from a parallel/independent task. `quote`, if
    # given, makes the reply quote (reference) the user's triggering message , 
    # only the first chunk quotes it, so a multi-part answer reads naturally.
    parts = chunks(text)
    total = len(parts)
    ids = []
    with _send_lock:
        for i, part in enumerate(parts, 1):
            head = REPLY_HEADER
            if tag:
                head += f" [{tag}]"
            if total > 1:
                head += f" ({i}/{total})"
            sent = send(group_jid, f"{head}\n\n{part}\n\n{REPLY_HEADER}",
                        quote=quote if i == 1 else None)
            if isinstance(sent, str) and sent:
                ids.append(sent)
    return ids


# ----------------------------------------------------------------------------
# Claude
# ----------------------------------------------------------------------------
# Failures worth another try: the API refused or died on *this* attempt, not
# something a rerun would hit identically. The output content filter is in here
# on purpose, it fires on what a particular run happened to emit (a big inline
# base64 icon, a wall of vector-path data), so the same request usually
# succeeds once the run stops trying to print the blob.
_RETRYABLE_ERR = re.compile(
    r"content filtering|overloaded|rate.?limit|too many requests|"
    r"\b(?:429|500|502|503|504|529)\b|internal server|"
    r"timed? ?out|connection (?:reset|refused|error)|network", re.I)


def _one_claude_run(prompt, model, system, timeout, effort):
    """One CLI invocation. Returns (text, error, retryable)."""
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "json"]
    if system:
        cmd += ["--append-system-prompt", system]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    cmd += PERMISSION_ARGS
    try:
        proc = subprocess.run(cmd, cwd=CLAUDE_CWD, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"Timed out after {timeout}s.", False
    # The CLI still prints a complete JSON result when it exits non-zero: an API
    # error lands in `result`, not on stderr. Parse before judging the exit
    # code, or a one-line "blocked by content filtering" reaches the user as 500
    # characters of raw JSON tail (which then also got stored as history).
    data = None
    if (proc.stdout or "").lstrip().startswith("{"):
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            data = None
    if data is not None and (proc.returncode != 0 or data.get("is_error")):
        msg = str(data.get("result") or data.get("error")
                  or f"claude exited {proc.returncode}").strip()
        return None, msg, bool(_RETRYABLE_ERR.search(msg))
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[-300:]
        return None, f"claude exited {proc.returncode}: {err}", True
    if data is None:
        return None, f"Could not parse claude output: {(proc.stdout or '')[:200]}", True
    return data.get("result", ""), None, False


def _user_facing_error(msg):
    """A short line the user can act on, never a raw dump."""
    m = msg.lower()
    if "content filtering" in m:
        return ("(The API blocked this run's output every time, so nothing came "
                "back. It usually trips on a huge inline blob, like a base64 image or "
                "a wall of icon path data. Say \"retry\" and I'll go again.)")
    if "overload" in m or "rate" in m or "529" in m:
        return "(The API stayed overloaded across every retry. Say \"retry\".)"
    if "timed out" in m or "timeout" in m:
        return f"({msg[:200]})"
    return f"(Run failed: {msg[:200]})"


def run_claude(prompt, model=None, system=SYSTEM_PROMPT, timeout=CLAUDE_TIMEOUT,
               effort=None, retries=CLAUDE_RETRIES):
    """Run claude, retrying transient API failures.

    `prompt` may be a string, or a callable taking the 1-based attempt number so
    the caller can rebuild it (smaller history, extra instructions) after a
    failure. Errors come back already phrased for the user.
    """
    last = "unknown error"
    for attempt in range(1, max(1, retries) + 1):
        p = prompt(attempt) if callable(prompt) else prompt
        text, err, retryable = _one_claude_run(p, model, system, timeout, effort)
        if err is None:
            if attempt > 1:
                log(f"claude run recovered on attempt {attempt}")
            return text, None
        last = err
        give_up = not retryable or attempt >= max(1, retries)
        log(f"claude run failed (attempt {attempt}/{max(1, retries)},",
            "giving up)" if give_up else "retrying)", "|", err[:160])
        if give_up:
            break
        time.sleep(CLAUDE_RETRY_WAIT * attempt)
    return None, _user_facing_error(last)


# ----------------------------------------------------------------------------
# Thinking level ("effort") detection
# ----------------------------------------------------------------------------
# Words showing the user is talking about *how hard to think*, so that a bare
# "low"/"max" in an ordinary sentence ("low battery") can't quietly change the
# effort of the run.
_EFFORT_CONTEXT = re.compile(
    r"think\w*|thought|effort|intel\w*|smart\w*|brain\w*|reason\w*|\biq\b|"
    r"genius|\bmodel\b|\bclaude\b|\bmode\b|\blevel\b|\bpower\b|\bdumb\b|"
    r"חשיב\w*|תחשוב|מאמץ|חכם|מוח|טיפש")

# Narrower signal: the user is plainly discussing brainpower, but in words the
# token table below doesn't cover. Only these are worth a model call.
# Deliberately excludes a bare "think" so a casual "i think we should…" is free.
_EFFORT_HINT = re.compile(
    r"\beffort\b|thinking\s+level|intel\w*|\bsmart\w*|\bbrain\w*|\biq\b|"
    r"\bgenius\b|\bultra\b|\bdumb\b|think\s+(?:hard|less|more|deep)|"
    r"\b(?:over|under)[\s\-]?think\w*|\ball\s+out\b|\bbest\s+model\b|"
    r"\bstrongest\b|\bsmartest\b|רמת\s+חשיבה|תחשוב|הכי\s+חכם")

# (level, pattern, needs a context word within ±CTX_WINDOW chars).
# Patterns that can only mean brainpower fire on their own.
_EFFORT_TOKENS = [
    ("max",    re.compile(r"ultra[\s\-]?think\w*"),                     False),
    ("max",    re.compile(r"\bmax(?:imum)?\b|מקסימום"),                  True),
    # An intensifier in front of "harder" is the top of the scale: "think much
    # harder" outranks a plain "think harder". Listed above the xhigh row to
    # keep the ladder readable; highest-wins would settle it either way.
    ("max",    re.compile(r"think\w*\s+(?:much|way|far|even|a\s+lot|"
                          r"so\s+much)\s+harder"),                      False),
    ("xhigh",  re.compile(r"\bx[\s\-]?high\b|\bextra[\s\-]?high\b"),    False),
    ("xhigh",  re.compile(r"think\w*\s+harder|"
                          r"think\w*\s+(?:really|very|super)\s+hard"),  False),
    ("high",   re.compile(r"think\w*\s+hard\b"),                        False),
    ("high",   re.compile(r"\bhigh\b|גבוה"),                             True),
    ("medium", re.compile(r"\bmedium\b|\bmid\b|\bnormal\b|בינוני"),      True),
    ("low",    re.compile(r"\blow\b|\bminimal\b|נמוך"),                  True),
]
CTX_WINDOW = 40


def _near_context(text, start, end):
    return bool(_EFFORT_CONTEXT.search(
        text[max(0, start - CTX_WINDOW):end + CTX_WINDOW]))


def _regex_effort(text):
    """Highest level the user explicitly named, or None. Highest wins because
    people escalate ('not high, use max'), they rarely walk a level back."""
    t = text.lower()
    found = [level for level, pat, needs_ctx in _EFFORT_TOKENS
             for m in pat.finditer(t)
             if not needs_ctx or _near_context(t, m.start(), m.end())]
    return max(found, key=EFFORT_LEVELS.index) if found else None


def _model_effort(text):
    """Last resort for phrasings the token table misses ('use your biggest
    brain'). Returns a level or None."""
    prompt = ("A user sent this message to their assistant:\n---\n" + text +
              "\n---\n\nDid the user ask for a particular thinking level / "
              "effort / intelligence for handling it?\n"
              "Answer with exactly one word: low, medium, high, xhigh, max, or "
              "none.\n\n"
              "The test is whether the user is steering how much brainpower to "
              "spend, in either direction:\n"
              "- Asking you to be smarter / think more than you normally would "
              "-> xhigh or max ('use your biggest brain', 'be the smartest you "
              "can', 'really chew on this').\n"
              "- Asking you to think less or answer cheaply -> low ('don't "
              "overthink it', 'quick answer, no deep dive').\n"
              "- Merely wanting the job done well is NOT steering brainpower -> "
              "none ('do it right', 'no mistakes', 'be careful', 'make it "
              "good').\n"
              "When unsure, answer none.")
    sys_p = "You are a terse classifier. Output exactly one word and nothing else."
    out, err = run_claude(prompt, model=EFFORT_MODEL, system=sys_p,
                          timeout=EFFORT_TIMEOUT, effort="low")
    if err or not out:
        log("effort detect fallback:", err)
        return None
    word = out.strip().lower().strip(".")
    # Check xhigh before high: "high" is a prefix of neither, but be explicit.
    for level in ("xhigh", "max", "medium", "high", "low"):
        if word == level:
            return level
    return None


def detect_effort(text):
    """(level, reason). Defaults to DEFAULT_EFFORT when the user didn't say."""
    level = _regex_effort(text)
    if level:
        return level, "explicit"
    if _EFFORT_HINT.search(text.lower()):
        level = _model_effort(text)
        if level:
            return level, "inferred"
    return DEFAULT_EFFORT, "default"


def classify(content):
    """Decide whether a message continues the in-flight thread or is independent.
    Returns 'followup' or 'new'. Conservative: defaults to 'followup'."""
    hist = history_text(limit=10)
    prompt = (
        "You are a router for a WhatsApp assistant that may be busy on a task.\n"
        "Recent chat history:\n" + hist + "\n\n"
        "New message from the user:\n" + content + "\n\n"
        "Is this new message a FOLLOWUP that continues / corrects / depends on the "
        "most recent task or topic, or a NEW independent request that could be "
        "handled on its own in parallel?\n"
        "Answer with exactly one word: followup or new.")
    sys_p = "You are a terse classifier. Output exactly one word and nothing else."
    out, err = run_claude(prompt, model=CLASSIFY_MODEL, system=sys_p,
                          timeout=CLASSIFY_TIMEOUT)
    if err or not out:
        log("classify fallback (followup):", err)
        return "followup"
    word = out.strip().lower()
    return "new" if word.startswith("new") else "followup"


def ts_age(ts):
    """Seconds since `ts` (bridge timestamp string), or None if unusable."""
    ts = (ts or "").strip()
    if not ts:
        return None
    try:
        sent = datetime.datetime.fromisoformat(ts)
    except ValueError:
        return None
    if sent.tzinfo is None:
        sent = sent.astimezone()
    return max(0.0, (datetime.datetime.now(sent.tzinfo) - sent).total_seconds())


def msg_age(msg):
    """Seconds since the user hit send, or None if the timestamp is unusable."""
    return ts_age(msg.get("ts"))


def already_seen(state, msg_id):
    """True if this message id was handled before (see REPLAY_MAX_AGE note)."""
    return bool(msg_id) and msg_id in state.get("seen_ids", [])


def mark_seen(state, msg_id):
    if not msg_id:
        return
    seen = state.setdefault("seen_ids", [])
    seen.append(msg_id)
    if len(seen) > SEEN_IDS_KEEP:
        del seen[:-SEEN_IDS_KEEP]


def _age_note(msg):
    # A run that isn't told the message is old will answer an hours-stale
    # question in the present tense, or "helpfully" diagnose the lateness as a
    # replay bug. Both have happened. State the age instead of implying "now".
    age = msg_age(msg)
    if age is None or age < STALE_AFTER:
        return ""
    mins = int(age // 60)
    when = ""
    try:
        when = datetime.datetime.fromisoformat(msg["ts"]).strftime(" at %H:%M")
    except (ValueError, KeyError):
        pass
    return (f" [sent{when}, {mins} min ago, it waited in the queue behind a "
            f"long job. It is NOT a duplicate or a replay; it is a real "
            f"message you have not answered yet. Answer it, and don't open by "
            f"telling the user it is stale.]")


# Handed to a retry so it doesn't reproduce whatever the API refused to let
# through. Emitting an icon or an image inline is the usual culprit, and it is
# never necessary: a run can write the file and say one sentence about it.
RETRY_NOTE = (
    "\n\n[An earlier attempt at this exact message died before it could answer "
    "(the API blocked the run's output). Do the work as asked, but do not print "
    "large encoded blobs in your reply or in your thinking: no base64, no long "
    "vector-path or icon data, no dumping whole files. Write that content "
    "straight to disk with a script instead, and keep the reply short prose.]")


def build_prompt(msgs, hist_limit=HISTORY_LIMIT, note=""):
    hist = history_text(limit=hist_limit)
    if len(msgs) == 1:
        head = ("The user just sent this new message, reply to it"
                + _age_note(msgs[0]) + ":\n" + msgs[0]["content"])
    else:
        # Coalesced burst: one reply covering all of them beats N stale replies.
        lines = [f"{i}. {m['content']}{_age_note(m)}"
                 for i, m in enumerate(msgs, 1)]
        head = (f"The user sent these {len(msgs)} messages in a row, oldest "
                "first. They are all unanswered and are most likely one train "
                "of thought. Write ONE reply that covers all of them:\n"
                + "\n\n".join(lines))
    return ("Recent WhatsApp chat history with this user (oldest first), for "
            "context only:\n"
            "--------\n" + hist + "\n--------\n\n" + head + note)


# ----------------------------------------------------------------------------
# Routing / workers
# ----------------------------------------------------------------------------
INTAKE = queue.Queue()          # raw incoming messages awaiting routing
FOLLOWUP_Q = queue.Queue()      # serial thread for followups
PARALLEL_SEM = threading.Semaphore(MAX_PARALLEL)

_inflight_lock = threading.Lock()
_inflight = 0


def _inc():
    global _inflight
    with _inflight_lock:
        _inflight += 1


def _dec():
    global _inflight
    with _inflight_lock:
        _inflight -= 1


def busy():
    with _inflight_lock:
        if _inflight > 0:
            return True
    return not FOLLOWUP_Q.empty() or not INTAKE.empty()


def process(msgs, group_jid, tag=""):
    # msgs is a list of dicts: {"content", "id", "sender", "ts"}, one message,
    # or a coalesced burst answered by a single run. We quote the newest one so
    # the reply lands under it, and mark every message in the batch, because a
    # message that only ever got 👀 reads as dropped.
    if isinstance(msgs, dict):
        msgs = [msgs]
    ids = [m.get("id", "") for m in msgs if m.get("id")]
    newest = msgs[-1]
    quote = {"id": newest.get("id", ""), "sender": newest.get("sender", ""),
             "text": newest["content"]}
    _inc()
    try:
        # The whole batch runs at the highest level any one message asked for:
        # a burst ending in "think harder" means the user wants it all harder.
        efforts = [detect_effort(m["content"]) for m in msgs]
        effort, why = max(efforts, key=lambda e: EFFORT_LEVELS.index(e[0]))
        log("effort:", effort, f"({why})", f"batch={len(msgs)}", "|",
            newest["content"][:60].replace("\n", " "))
        # Surface the level in the reply header, but only when it isn't the
        # usual one, so the user can see the detection landed.
        if effort != DEFAULT_EFFORT:
            tag = f"{tag} {effort}".strip()
        def prompt_for(attempt):
            if attempt == 1:
                return build_prompt(msgs)
            # Retrying after a blocked/failed run. Shrink the history window
            # (less surface for the filter to catch on, and cheaper) and tell
            # the run what not to emit.
            return build_prompt(msgs,
                                hist_limit=max(5, HISTORY_LIMIT >> (attempt - 1)),
                                note=RETRY_NOTE)

        result, err = run_claude(prompt_for,
                                 model=(WORK_MODEL or None), effort=effort)
        text = err if err else (result or "(empty reply)")
        if not err and SILENT_MARKER and text.strip().startswith(SILENT_MARKER):
            log("silent run: nothing sent |", text.strip()[:120].replace("\n", " "))
            append_history("assistant", "(sent nothing: passed the message on)")
            for mid in ids:
                react(group_jid, mid, DONE_EMOJI)
            return
        sent_ids = reply(group_jid, text, tag=tag, quote=quote)
        # Only real answers become history. Storing the failure text meant every
        # later run read the crash dump back as context, so one blocked run made
        # the next prompt worse.
        if not err:
            append_history("assistant", text, sent_ids)
        # Only claim ✅ once the answer is actually out; a failed run gets ❌ so
        # "done" never means "died quietly".
        done = ERROR_EMOJI if err else DONE_EMOJI
        for mid in ids:
            react(group_jid, mid, done)
    except Exception as e:
        log("ERROR process:", e)
        try:
            reply(group_jid, f"(internal error: {e})", quote=quote)
        except Exception:
            pass
        for mid in ids:
            react(group_jid, mid, ERROR_EMOJI)
    finally:
        _dec()


def drain_followups(first):
    """first + everything already queued behind it, up to COALESCE_MAX."""
    batch = [first]
    while len(batch) < COALESCE_MAX:
        try:
            batch.append(FOLLOWUP_Q.get_nowait())
        except queue.Empty:
            break
    return batch


def followup_worker(group_jid):
    while True:
        msg = FOLLOWUP_Q.get()
        try:
            # Answering a burst one message at a time means each reply costs a
            # full run, so message 4 gets answered half an hour late against a
            # conversation that has already moved on. Take everything waiting
            # and answer it once, together.
            batch = drain_followups(msg)
            if len(batch) > 1:
                log(f"coalescing {len(batch)} queued messages into one run")
            process(batch, group_jid)
        except Exception as e:
            log("ERROR followup_worker:", e)


def dispatcher(group_jid):
    while True:
        msg = INTAKE.get()
        content = msg["content"]
        try:
            cmd_resp = handle_command(content)
            if cmd_resp is not None:
                cmd_ids = reply(group_jid, cmd_resp, quote={"id": msg.get("id", ""),
                                "sender": msg.get("sender", ""), "text": content})
                append_history("assistant", cmd_resp, cmd_ids)
                react(group_jid, msg.get("id", ""), DONE_EMOJI)
                continue
            # Only pay for the judge when something is actually in flight;
            # otherwise just run it now on the serial worker.
            route = classify(content) if busy() else "followup"
            log("route:", route, "|", content[:60].replace("\n", " "))
            if route == "new":
                t = threading.Thread(target=_run_parallel,
                                     args=(msg, group_jid), daemon=True)
                t.start()
            else:
                FOLLOWUP_Q.put(msg)
        except Exception as e:
            log("ERROR dispatcher:", e)


def _run_parallel(msg, group_jid):
    with PARALLEL_SEM:
        process(msg, group_jid, tag="parallel")


# ----------------------------------------------------------------------------
# Commands handled locally
# ----------------------------------------------------------------------------
HELP = ("Commands:\n"
        "/help, this message\n"
        "/ping, health check\n"
        "/new or /reset, clear the remembered chat history\n"
        "Anything else is sent to Claude (with recent history as context).\n\n"
        f"Receipts: your message gets {ACK_EMOJI} when I pick it up and "
        f"{DONE_EMOJI} once the reply is sent ({ERROR_EMOJI} if the run failed).\n\n"
        f"Thinking level: say one in your message (\"xhigh\", \"ultrathink\", "
        f"\"low effort\") and it runs at that. Otherwise it defaults to "
        f"{DEFAULT_EFFORT}.")


def handle_command(text):
    cmd = text.strip().lower()
    if cmd in ("/help", "help"):
        return HELP
    if cmd == "/ping":
        return "pong ✅"
    if cmd in ("/new", "/reset"):
        try:
            open(HISTORY_FILE, "w").close()
        except Exception:
            pass
        return "Cleared the remembered chat history."
    return None


# ----------------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------------
def main():
    log(f"claude-whatsapp-bot v2 starting. group={GROUP_NAME!r}")
    state = load_state()

    group_jid = state.get("group_jid")
    while not group_jid:
        try:
            with db() as conn:
                group_jid = resolve_group_jid(conn)
        except Exception as e:
            log("WARN db not ready:", e)
        if not group_jid:
            log(f"Waiting for group {GROUP_NAME!r} to appear...")
            time.sleep(5)
    state["group_jid"] = group_jid
    log("Group JID:", group_jid)

    seed_history_from_bridge(group_jid)

    if "last_rowid" not in state:
        with db() as conn:
            state["last_rowid"] = max_rowid(conn)
        log("Initialized at rowid", state["last_rowid"], "(ignoring backlog)")

    # Cold start (or a first run after this guard was added): treat everything
    # already in the chat as answered. Otherwise a later history sync re-inserts
    # those messages with new rowids and they look brand new.
    if "seen_ids" not in state:
        with db() as conn:
            rows = conn.execute(
                "SELECT id FROM messages WHERE chat_jid = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (group_jid, SEEN_IDS_KEEP)).fetchall()
        state["seen_ids"] = [r[0] for r in reversed(rows) if r[0]]
        log("Seeded", len(state["seen_ids"]), "seen message ids")
    save_state(state)

    # background workers
    threading.Thread(target=dispatcher, args=(group_jid,), daemon=True).start()
    threading.Thread(target=followup_worker, args=(group_jid,), daemon=True).start()

    reply(group_jid, "online ✅ (v2: windowed history + parallel routing). /help for commands")

    beat()
    poll_failures = 0
    while True:
        try:
            with db() as conn:
                jid = resolve_group_jid(conn) or group_jid
                if jid != group_jid:
                    log("Group JID changed:", group_jid, "->", jid)
                    group_jid = state["group_jid"] = jid
                rows = new_messages(conn, group_jid, state["last_rowid"])
            poll_failures = 0
            beat()
        except Exception as e:
            poll_failures += 1
            log(f"ERROR db poll ({poll_failures}/{MAX_POLL_FAILURES}):", e)
            # Retrying forever in-process is how this bot stayed deaf for hours:
            # it kept logging while nobody read the log. Bail out instead and let
            # systemd's Restart=always give us a clean process.
            if poll_failures >= MAX_POLL_FAILURES:
                log("Too many consecutive poll failures; exiting for a restart.")
                sys.exit(1)
            time.sleep(POLL_INTERVAL)
            continue

        for rowid, msg_id, content, media_type, sender, ts in rows:
            state["last_rowid"] = rowid          # claim immediately (no reprocessing)
            content = (content or "").strip()

            if content.startswith(REPLY_MARKER):  # our own reply echoed back
                mark_seen(state, msg_id)
                save_state(state)
                continue

            # Anything sent THROUGH this bridge (store watcher, quota watcher,
            # any script hitting /api/send) is stored by the bridge itself with a
            # nanosecond time.Now() stamp. What the owner types arrives from WhatsApp
            # with whole seconds. So a fractional timestamp means a tool wrote
            # it from the owner's number, not the owner: never answer it.
            if re.search(r":\d\d\.\d", ts or ""):
                mark_seen(state, msg_id)
                save_state(state)
                log(f"skip tool-sent: {content[:50]}")
                continue

            if content.startswith(IGNORE_PREFIXES):  # another tool's notice
                mark_seen(state, msg_id)
                save_state(state)
                log(f"skip notice: {content[:50]}")
                continue

            # Replay guard: a history sync (after a re-link) re-inserts old
            # messages with fresh rowids. Record them, never answer them.
            age = ts_age(ts)
            if already_seen(state, msg_id):
                save_state(state)
                continue
            if age is not None and age > REPLAY_MAX_AGE:
                mark_seen(state, msg_id)
                save_state(state)
                log(f"skip replay (age {int(age // 3600)}h): {content[:60]}")
                continue

            mark_seen(state, msg_id)
            save_state(state)
            if not content:
                if media_type:
                    react(group_jid, msg_id, ACK_EMOJI)
                    reply(group_jid,
                          f"(I can't read '{media_type}' messages yet, send text.)",
                          quote={"id": msg_id, "sender": sender,
                                 "text": f"({media_type})"})
                    react(group_jid, msg_id, DONE_EMOJI)
                continue

            append_history("user", content, [msg_id])
            # 👀 as early as possible: this is the "I got it" receipt, so it has to
            # land at intake, not when the run starts. A message queued behind a
            # long job would otherwise sit unacknowledged for the whole job.
            react(group_jid, msg_id, ACK_EMOJI)
            # Hand off the full message so the reply can quote it; keep polling.
            INTAKE.put({"content": content, "id": msg_id, "sender": sender,
                        "ts": ts})

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("bye")
        sys.exit(0)
