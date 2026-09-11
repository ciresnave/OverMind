# SPDX-License-Identifier: MIT OR Apache-2.0
# PTY LIFECYCLE MEASUREMENT — (a) idle detection (b) does /clear actually forget (c) does the process survive.
import sys, time, threading, re
import winpty, pyte

COLS, ROWS = 120, 40
CWD = r"C:\Users\cires\AppData\Local\Temp\claude\C--Projects-OverMind\1a854350-47ce-4a61-85cf-a890f3dc6c52\scratchpad\puppet"
LOG = open(r"C:\Projects\OverMind\probe\drive.log", "w", encoding="utf-8")
SECRET = "ZEPHYR-4417-QUOKKA"

screen = pyte.Screen(COLS, ROWS); stream = pyte.Stream(screen)
lock = threading.Lock(); running = True
raw = []                      # every chunk, in order
nbytes = [0]; last_data_at = [time.time()]

proc = winpty.PtyProcess.spawn(["claude", "--model", "haiku"], dimensions=(ROWS, COLS), cwd=CWD)
PID0 = proc.pid

def reader():
    while running:
        try: data = proc.read(4096)
        except Exception: break
        if data:
            with lock:
                raw.append(data); nbytes[0] += len(data); last_data_at[0] = time.time()
                stream.feed(data)
        else: time.sleep(0.02)
threading.Thread(target=reader, daemon=True).start()

def render():
    with lock: return "\n".join(r.rstrip() for r in screen.display)
def rawlen():
    with lock: return sum(len(c) for c in raw)
def raw_since(off):
    with lock: return "".join(raw)[off:]
def say(m):
    LOG.write(m + "\n"); LOG.flush()
def dump(tag):
    say(f"\n{'='*16} {tag} alive={proc.isalive()} pid={proc.pid} bytes={nbytes[0]} {'='*16}")
    say(render())

def wait_quiet(quiet=1.5, timeout=120, label=""):
    """IDLE DETECTOR: no bytes from the PTY for `quiet` seconds."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if time.time() - last_data_at[0] >= quiet:
            say(f"[idle] {label}: quiet {quiet}s reached after {time.time()-t0:.1f}s, bytes={nbytes[0]}")
            return True
        time.sleep(0.1)
    say(f"[idle] {label}: TIMEOUT after {timeout}s"); return False

def send(text, enter=True):
    proc.write(text + ("\r" if enter else ""))
    time.sleep(0.4)

# ---- 0. trust prompt -------------------------------------------------------
wait_quiet(1.5, 40, "boot")
dump("BOOT")
if "trust this folder" in render():
    send("\x1b[B", enter=False)   # down arrow
    time.sleep(0.3); send("\r", enter=False)
    say("[step] answered trust prompt: Yes")
    wait_quiet(2.0, 60, "post-trust")
dump("AFTER-TRUST")

# ---- 1. WORKING vs IDLE ----------------------------------------------------
off = rawlen()
send(f"Reply with exactly: ACK. Also remember this token for later: {SECRET}")
time.sleep(1.0)
say(f"[working-probe] 1.0s after submit: seconds_since_last_byte={time.time()-last_data_at[0]:.2f}")
mid = render()
say("[working-probe] screen mid-flight contains 'esc to interrupt': " + str("esc to interrupt" in mid))
wait_quiet(2.0, 120, "answer-1")
dump("AFTER-SECRET-GIVEN")

# ---- 2. POSITIVE CONTROL: the token IS retrievable before the clear --------
off_ctrl = rawlen()
send("What token did I ask you to remember? Reply with just the token.")
wait_quiet(2.0, 120, "answer-2")
ctrl_bytes = raw_since(off_ctrl)
CONTROL_HIT = SECRET in ctrl_bytes
say(f"\n[CONTROL] token present in pre-clear answer bytes: {CONTROL_HIT}")
dump("CONTROL-RECALL")

# ---- 3. /clear -------------------------------------------------------------
pid_before = proc.pid; alive_before = proc.isalive()
off_clear = rawlen()
send("/clear")
wait_quiet(2.0, 60, "post-clear")
alive_after = proc.isalive(); pid_after = proc.pid
say(f"\n[C] alive_before={alive_before} pid_before={pid_before} | alive_after={alive_after} pid_after={pid_after}")
dump("AFTER-CLEAR")

# ---- 4. SUBJECT: is the token gone? ---------------------------------------
off_test = rawlen()
send("What token did I ask you to remember? Reply with just the token, or the single word UNKNOWN.")
wait_quiet(2.0, 120, "answer-3")
test_bytes = raw_since(off_test)
SUBJECT_HIT = SECRET in test_bytes
say(f"\n[SUBJECT] token present in post-clear answer bytes: {SUBJECT_HIT}")
say(f"[SUBJECT] post-clear answer bytes len={len(test_bytes)}")
dump("AFTER-CLEAR-RECALL-ATTEMPT")

say("\n" + "#"*60)
say(f"RESULT idle_detector=quiet-bytes  control_recall={CONTROL_HIT}  post_clear_recall={SUBJECT_HIT}")
say(f"RESULT process_survived_clear={alive_after}  pid_stable={pid_before==pid_after==PID0}")
say("#"*60)

running = False
try: proc.terminate(force=True)
except Exception: pass
LOG.close(); print("done")
