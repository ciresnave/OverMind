# SPDX-License-Identifier: MIT OR Apache-2.0
# LIFECYCLE v4 - submit is VERIFIED by observing the working state, not assumed.
import time, threading, re, sys
import winpty, pyte

COLS, ROWS = 120, 40
CWD = r"C:\Users\cires\AppData\Local\Temp\claude\C--Projects-OverMind\1a854350-47ce-4a61-85cf-a890f3dc6c52\scratchpad\puppet"
LOG = open(r"C:\Projects\OverMind\probe\v4.log", "w", encoding="utf-8")
RAW = open(r"C:\Projects\OverMind\probe\v4.raw", "w", encoding="utf-8")
SECRET = "ZEPHYR-4417-QUOKKA"

screen = pyte.Screen(COLS, ROWS); stream = pyte.Stream(screen)
lock = threading.Lock(); running = True; raw = []
proc = winpty.PtyProcess.spawn(["claude", "--model", "haiku"], dimensions=(ROWS, COLS), cwd=CWD)
PID0 = proc.pid

def rd():
    while running:
        try: d = proc.read(4096)
        except Exception: break
        if d:
            with lock: raw.append(d); stream.feed(d); RAW.write(d); RAW.flush()
        else: time.sleep(0.02)
threading.Thread(target=rd, daemon=True).start()

def render():
    with lock: return "\n".join(x.rstrip() for x in screen.display)
def rawtext():
    with lock: return "".join(raw)
def say(m): LOG.write(str(m) + "\n"); LOG.flush()
def dump(tag):
    say("\n" + "="*12 + " %s alive=%s pid=%s rawlen=%d " % (tag, proc.isalive(), proc.pid, len(rawtext())) + "="*12)
    say(render())
def working(): return "esc to interrupt" in render().lower()
def wait_for(pred, timeout, label):
    t0 = time.time()
    while time.time()-t0 < timeout:
        if pred(): say("[wait] %s OK %.1fs" % (label, time.time()-t0)); return True
        time.sleep(0.1)
    say("[wait] %s TIMEOUT %ss" % (label, timeout)); return False
def stable(secs, timeout, label):
    t0=time.time(); last=render(); since=time.time()
    while time.time()-t0 < timeout:
        cur=render()
        if cur!=last: last=cur; since=time.time()
        elif time.time()-since>=secs and not working():
            say("[idle] %s stable %.1fs (total %.1fs)" % (label, secs, time.time()-t0)); return True
        time.sleep(0.1)
    say("[idle] %s TIMEOUT" % label); return False

def submit(text, label):
    """Type, confirm the box shows it, THEN send Enter as its own write, THEN confirm work started."""
    proc.write(text)
    probe = text[:28]
    typed = wait_for(lambda: probe in render().replace("\n", " ") or probe in render(), 15, label+":typed")
    time.sleep(0.4)
    proc.write("\r")
    started = wait_for(working, 20, label+":started-working")
    if not started:
        say("[submit] %s: no working state seen; screen now:" % label); say(render())
    stable(3.0, 240, label+":settled")
    return started

def answer_block():
    """The assistant's rendered output: lines after the last bullet, excluding the input box."""
    lines = render().split("\n")
    idxs = [i for i,l in enumerate(lines) if l.strip().startswith("\u25cf")]
    if not idxs: return ""
    start = idxs[-1]
    out = []
    for l in lines[start:]:
        if l.strip().startswith("\u276f") or "esc to interrupt" in l.lower(): break
        out.append(l)
    return "\n".join(out)

had = wait_for(lambda: "trust this folder" in render(), 20, "trust")
if had:
    for _ in range(6):
        if "Yes, I trust" in render(): 
            ln = [l for l in render().split("\n") if l.strip().startswith("\u276f")]
            if ln and "Yes, I trust" in ln[0]: break
        proc.write("\x1bOB"); time.sleep(1.2)
    proc.write("\r")
stable(3.0, 120, "boot"); dump("READY")

# ---- turn 1: plant the token ----
submit("Reply with exactly ACK. Remember this token for later: " + SECRET, "T1")
dump("T1-DONE")

# ---- turn 2: POSITIVE CONTROL (question contains no token) ----
submit("What token did I ask you to remember? Reply with only the token.", "T2")
a2 = answer_block()
CONTROL = SECRET in a2
say("[CONTROL] answer_block=%r" % a2[:300])
say("[CONTROL] token in ANSWER BLOCK before clear: %s" % CONTROL)
dump("T2-DONE")

# ---- /clear ----
alive_b, pid_b = proc.isalive(), proc.pid
proc.write("/clear"); time.sleep(0.6); proc.write("\r")
stable(3.0, 90, "clear")
alive_a, pid_a = proc.isalive(), proc.pid
say("[C] before alive=%s pid=%s | after alive=%s pid=%s | spawn=%s" % (alive_b,pid_b,alive_a,pid_a,PID0))
say("[C] SECRET still on screen right after clear: %s" % (SECRET in render()))
dump("AFTER-CLEAR")

# ---- turn 3: SUBJECT ----
submit("What token did I ask you to remember? Reply with only the token, or the single word UNKNOWN.", "T3")
a3 = answer_block()
SUBJECT = SECRET in a3
say("[SUBJECT] answer_block=%r" % a3[:300])
say("[SUBJECT] token in ANSWER BLOCK after clear: %s" % SUBJECT)
dump("T3-DONE")

say("#"*60)
say("RESULT control_recall_before_clear=%s" % CONTROL)
say("RESULT recall_after_clear=%s" % SUBJECT)
say("RESULT process_survived_clear=%s pid_stable=%s" % (alive_a, pid_b==pid_a==PID0))
say("#"*60)
RAW.close(); running=False
try: proc.terminate(force=True)
except Exception: pass
LOG.close(); print("done")
