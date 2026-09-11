# SPDX-License-Identifier: MIT OR Apache-2.0
# Learn the READY and WORKING screen signatures. Positive screen predicate, not byte-quiet.
import time, threading
import winpty, pyte
COLS, ROWS = 120, 40
CWD = r"C:\Users\cires\AppData\Local\Temp\claude\C--Projects-OverMind\1a854350-47ce-4a61-85cf-a890f3dc6c52\scratchpad\puppet"
LOG = open(r"C:\Projects\OverMind\probe\learn.log","w",encoding="utf-8")
screen = pyte.Screen(COLS, ROWS); stream = pyte.Stream(screen)
lock = threading.Lock(); running=True; nbytes=[0]
proc = winpty.PtyProcess.spawn(["claude","--model","haiku"], dimensions=(ROWS,COLS), cwd=CWD)
def reader():
    while running:
        try: d = proc.read(4096)
        except Exception: break
        if d:
            with lock: nbytes[0]+=len(d); stream.feed(d)
        else: time.sleep(0.02)
threading.Thread(target=reader,daemon=True).start()
def render():
    with lock: return "\n".join(r.rstrip() for r in screen.display)
def say(m): LOG.write(m+"\n"); LOG.flush()
def dump(tag): say(f"\n{'='*16} {tag} alive={proc.isalive()} bytes={nbytes[0]} {'='*16}"); say(render())
def wait_screen(pred, timeout, label):
    t0=time.time()
    while time.time()-t0<timeout:
        if pred(render()):
            say(f"[wait] {label} matched after {time.time()-t0:.1f}s bytes={nbytes[0]}"); return True
        time.sleep(0.15)
    say(f"[wait] {label} TIMEOUT {timeout}s bytes={nbytes[0]}"); return False

wait_screen(lambda s: "trust this folder" in s, 60, "trust-dialog")
dump("TRUST-DIALOG")
proc.write("\x1b[B"); time.sleep(0.4); proc.write("\r")
say("[step] sent Down + Enter")
time.sleep(6); dump("T+6s-after-trust")
time.sleep(6); dump("T+12s-after-trust")
time.sleep(8); dump("T+20s-after-trust")
running=False
try: proc.terminate(force=True)
except Exception: pass
LOG.close(); print("done")
