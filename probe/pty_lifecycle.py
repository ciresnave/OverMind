# LIFECYCLE MEASUREMENT v3 - raw stream captured to disk for offline analysis.
import time, threading
import winpty, pyte

COLS, ROWS = 120, 40
CWD = r"C:\Users\cires\AppData\Local\Temp\claude\C--Projects-OverMind\1a854350-47ce-4a61-85cf-a890f3dc6c52\scratchpad\puppet"
LOG = open(r"C:\Projects\OverMind\probe\lifecycle.log", "w", encoding="utf-8")
RAW = open(r"C:\Projects\OverMind\probe\lifecycle.raw", "w", encoding="utf-8")
SECRET = "ZEPHYR-4417-QUOKKA"
MARKS = []

screen = pyte.Screen(COLS, ROWS); stream = pyte.Stream(screen)
lock = threading.Lock(); running = True; raw = []
proc = winpty.PtyProcess.spawn(["claude", "--model", "haiku"], dimensions=(ROWS, COLS), cwd=CWD)
PID0 = proc.pid

def rd():
    while running:
        try: d = proc.read(4096)
        except Exception: break
        if d:
            with lock:
                raw.append(d); stream.feed(d); RAW.write(d); RAW.flush()
        else: time.sleep(0.02)
threading.Thread(target=rd, daemon=True).start()

def render():
    with lock: return "\n".join(x.rstrip() for x in screen.display)
def rawtext():
    with lock: return "".join(raw)
def say(m): LOG.write(m + "\n"); LOG.flush()
def mark(tag):
    n = len(rawtext()); MARKS.append((tag, n)); say(f"[mark] {tag} @ {n}")
def dump(tag):
    say(f"\n{'='*14} {tag} alive={proc.isalive()} pid={proc.pid} rawlen={len(rawtext())} {'='*14}")
    say(render())
def menu_line():
    for l in render().split("\n"):
        s = l.strip()
        if s.startswith("\u276f") and ("exit" in s or "trust" in s): return s
    return ""
def wait_for(pred, timeout, label):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred(): say(f"[wait] {label} OK after {time.time()-t0:.1f}s"); return True
        time.sleep(0.15)
    say(f"[wait] {label} TIMEOUT {timeout}s"); return False
def stable(secs=3.0, timeout=180, label=""):
    t0 = time.time(); last = render(); since = time.time()
    while time.time() - t0 < timeout:
        cur = render()
        if cur != last: last = cur; since = time.time()
        elif time.time() - since >= secs:
            say(f"[idle] {label}: stable {secs}s after {time.time()-t0:.1f}s"); return True
        time.sleep(0.15)
    say(f"[idle] {label}: TIMEOUT {timeout}s"); return False

had = wait_for(lambda: "trust this folder" in render(), 25, "trust-dialog")
dump("BOOT")
if had:
    for a in range(6):
        if "Yes, I trust" in menu_line(): break
        proc.write("\x1bOB"); time.sleep(1.2)
    say(f"[trust] VERIFIED before Enter: {menu_line()!r}")
    assert "Yes, I trust" in menu_line(), "unverified selection"
    proc.write("\r")
else:
    say("[trust] no dialog - directory already trusted")

stable(3.0, 120, "boot"); dump("READY")

mark("PRE_Q1")
proc.write("Reply with exactly ACK. Remember this token: " + SECRET + "\r")
time.sleep(1.5); dump("MID-FLIGHT")
stable(3.0, 180, "answer-1"); dump("AFTER-SECRET")

mark("PRE_CONTROL_Q")
off = len(rawtext())
proc.write("What token did I ask you to remember? Reply with just the token.\r")
stable(3.0, 180, "answer-2")
CONTROL = SECRET in rawtext()[off:]
say(f"[CONTROL] token recoverable BEFORE clear: {CONTROL}")
dump("CONTROL-RECALL")

mark("PRE_CLEAR")
alive_b, pid_b = proc.isalive(), proc.pid
proc.write("/clear\r")
stable(3.0, 90, "post-clear")
alive_a, pid_a = proc.isalive(), proc.pid
say(f"[C] before alive={alive_b} pid={pid_b} | after alive={alive_a} pid={pid_a} | spawn={PID0}")
dump("AFTER-CLEAR")

mark("PRE_POSTCLEAR_Q")
off2 = len(rawtext())
proc.write("What token did I ask you to remember? Reply with just the token, or the single word UNKNOWN.\r")
stable(3.0, 180, "answer-3")
post = rawtext()[off2:]
SUBJECT = SECRET in post
say(f"[SUBJECT] token present AFTER clear: {SUBJECT} (post bytes={len(post)})")
dump("AFTER-CLEAR-RECALL")
mark("END")

say("#"*60)
say(f"RESULT control_recall_before_clear={CONTROL}")
say(f"RESULT recall_after_clear={SUBJECT}")
say(f"RESULT process_survived_clear={alive_a} pid_stable={pid_b==pid_a==PID0}")
say("MARKS=" + repr(MARKS))
say("#"*60)
RAW.close(); running = False
try: proc.terminate(force=True)
except Exception: pass
LOG.close(); print("done")
