"""Drive an interactive `claude` session in a pty — the lab's only way to exercise a command
that cannot run headless.

    drive-interactive.py <cwd> <seconds> <prompts-file> [claude args...]

WHY. `claude -p` and the SDK cover every dispatched agent, and `dispatch-wave.sh` covers a
whole wave — but an INTERACTIVE command cannot be reached that way: `/design` stops at an
`AskUserQuestion`, and agent teams do not spawn teammates in `-p` at all (the docs say so),
so `/design-debate` has no headless form. Measured with this: the 0.10.31 arms, one
`/design` + audit against one `/design-debate`, on the same seeded lab epic.

WHAT IT DOES. Forks a pty, runs `claude` in it, and answers the TUI's dialogs by matching
the screen with escape codes and whitespace stripped: the folder-trust prompt, the renderer
offer, a Bash permission ask (option 1), an `AskUserQuestion` (the first option, which the
harness's commands write as the recommendation). Prompts are separated by a line of `---`
and sent one at a time, each when the previous turn has gone quiet. The text and its
newline go in SEPARATE writes — sent together the TUI takes them for a paste and the turn
never starts.

WHAT TO READ AFTERWARDS: the session transcript under ~/.claude/projects/<slug>/ (and its
`subagents/` directory, one file per teammate — where a teammate's cost is, since the
harness records none for them), the files the run wrote, and `checks/session-cost.sh` on
the lead's transcript. Never the screen log: it is a terminal's redraw, not a record.

ANSWERING A DIALOG IS A DECISION A PERSON WOULD HAVE MADE. This belongs in the lab, where
the epic is seeded and thrown away, and nowhere near a real repository."""
import os
import pty
import re as _re
import select
import signal
import sys
import time

cwd, seconds, prompt_file, *extra = sys.argv[1:]
seconds = int(seconds)
# several prompts, separated by a line that is exactly `---`; each is sent when the
# previous turn has finished (the input marker back, no busy indicator for a while)
prompts = [x.strip("\n") for x in open(prompt_file).read().split("\n---\n")]
prompt = prompts[0]
queue = prompts[1:]
env = dict(os.environ)
for k in ("CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_ENABLE_TASKS", "CLAUDECODE"):
    env.pop(k, None)
env.update({"TERM": "xterm-256color", "COLUMNS": "200", "LINES": "50"})
argv = ["claude", *extra]
pid, fd = pty.fork()
if pid == 0:
    os.chdir(cwd)
    os.execvpe(argv[0], argv, env)
log = open(os.path.join(os.path.dirname(prompt_file), "screen.log"), "wb")
start = time.time()
sent = False
recent = b""
answered = set()
last_repeat = {}
DIALOGS = {  # a fragment of the dialog's text (whitespace collapsed) -> what to send
    b"trustthisfolder": b"\r",
    b"fullscreenrenderer": b"2\r",
    b"Yes,Iaccept": b"2\r",
}
#: Prompts that recur (a permission ask, an AskUserQuestion): answer every time, not once.
REPEATED = {
    b"Doyouwanttoproceed?": b"\r",       # a Bash permission ask: option 1, Yes
    # AskUserQuestion: the first option, which /design writes as the recommendation.
    b"Entertoselect": b"\r",
}
def drain(timeout=0.2):
    global recent
    r, _, _ = select.select([fd], [], [], timeout)
    if fd in r:
        try:
            data = os.read(fd, 65536)
        except OSError:
            return False
        if not data:
            return False
        log.write(data)
        log.flush()
        recent = (recent + data)[-20000:]
    return True


def _plain(b: bytes) -> bytes:
    t = _re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\x1b[=>78]|\x1b\][^\x07]*\x07", b"", b)
    return _re.sub(rb"\s+", b"", t)
def answer_repeated():
    """A prompt that can appear many times — answered whenever the screen is showing it and
    it was not answered in the last few seconds."""
    global recent
    plain = _plain(recent[-4000:])
    now = time.time()
    for frag, keys in REPEATED.items():
        gap = 3 if frag == b"Entertoselect" else 8
        if frag in plain and now - last_repeat.get(frag, 0) > gap:
            time.sleep(0.6)
            os.write(fd, keys)
            last_repeat[frag] = now
            recent = b""
            print(f"[drive] answered {frag.decode()!r} at {now-start:.0f}s", flush=True)
            time.sleep(1.5)
            return True
    return False


def answer_dialogs():
    global recent
    plain = _plain(recent[-6000:])
    for frag, keys in DIALOGS.items():
        if frag in plain and frag not in answered:
            time.sleep(1.0)
            os.write(fd, keys)
            answered.add(frag)
            recent = b""
            print(f"[drive] answered dialog {frag.decode()!r} at {time.time()-start:.0f}s", flush=True)
            time.sleep(2.0)
            return True
    return False
try:
    while time.time() - start < seconds:
        if not drain():
            break
        if answer_dialogs():
            continue
        if answer_repeated():
            continue
        # the input box: the prompt is sent once the TUI shows its prompt marker and no dialog is pending
        if not sent and time.time() - start > 10 and "❯".encode() in recent and b"Entertoconfirm" not in _plain(recent[-4000:]):
            os.write(fd, prompt.encode())
            for _ in range(5):
                drain(0.2)
            time.sleep(0.8)
            os.write(fd, b"\r")
            sent = True
            sent_at = time.time()
            print(f"[drive] prompt sent at {time.time()-start:.0f}s", flush=True)
        # if the input box still shows the prompt text 8s after Enter, press Enter again
        if sent and time.time() - sent_at > 8 and not getattr(sys, "_nudged", False) and prompt[-12:].encode() in _plain(recent[-3000:]).replace(b" ", b""):
            os.write(fd, b"\r")
            sys._nudged = True
            print(f"[drive] nudged Enter at {time.time()-start:.0f}s", flush=True)
        # the next prompt, once the turn is over: idle marker visible and no busy indicator for 20s
        if sent and queue and time.time() - sent_at > 30:
            plain = _plain(recent[-3000:])
            busy = b"esctointerrupt" in plain or b"Esctointerrupt" in plain
            idle_marker = "❯".encode() in recent[-3000:]
            if idle_marker and not busy:
                quiet = getattr(sys, "_quiet_since", None)
                if quiet is None:
                    sys._quiet_since = time.time()
                elif time.time() - quiet > 20:
                    prompt = queue.pop(0)
                    os.write(fd, prompt.encode())
                    for _ in range(5):
                        drain(0.2)
                    time.sleep(0.8)
                    os.write(fd, b"\r")
                    sent_at = time.time()
                    sys._nudged = False
                    sys._quiet_since = None
                    print(f"[drive] next prompt sent at {time.time()-start:.0f}s", flush=True)
            else:
                sys._quiet_since = None
        if sent and not queue and time.time() - sent_at > 60:
            plain = _plain(recent[-3000:])
            if "❯".encode() in recent[-3000:] and b"esctointerrupt" not in plain and b"Esctointerrupt" not in plain:
                quiet = getattr(sys, "_done_since", None)
                if quiet is None:
                    sys._done_since = time.time()
                elif time.time() - quiet > 45:
                    print(f"[drive] all prompts answered; leaving at {time.time()-start:.0f}s", flush=True)
                    break
            else:
                sys._done_since = None
    os.write(fd, b"/exit\r")
    time.sleep(3)
finally:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
print(f"[drive] done after {time.time()-start:.0f}s", flush=True)
