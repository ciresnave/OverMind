# Which input encoding does the TUI's select list actually accept? Measure, don't guess.
import time, threading, sys
import winpty, pyte
CWD = r"C:\Users\cires\AppData\Local\Temp\claude\C--Projects-OverMind\1a854350-47ce-4a61-85cf-a890f3dc6c52\scratchpad\puppet"
LOG=open(r"C:\Projects\OverMind\probe\keys.log","w",encoding="utf-8")
screen=pyte.Screen(120,40); stream=pyte.Stream(screen); lock=threading.Lock(); running=True; nb=[0]
proc=winpty.PtyProcess.spawn(["claude","--model","haiku"],dimensions=(40,120),cwd=CWD)
def r():
    while running:
        try: d=proc.read(4096)
        except Exception: break
        if d:
            with lock: nb[0]+=len(d); stream.feed(d)
        else: time.sleep(0.02)
threading.Thread(target=r,daemon=True).start()
def render():
    with lock: return "\n".join(x.rstrip() for x in screen.display)
def say(m): LOG.write(m+"\n"); LOG.flush()
def sel():
    for line in render().split("\n"):
        if line.strip().startswith("\u276f"): return line.strip()
    return "<no cursor line>"
t0=time.time()
while time.time()-t0<60:
    if "trust this folder" in render(): break
    time.sleep(0.15)
say(f"dialog up at {time.time()-t0:.1f}s; selection={sel()!r} alive={proc.isalive()}")
trials=[("ESC[B (one write)","\x1b[B"),("ESCOB","\x1bOB"),("Tab","\t"),("j","j"),("ctrl-n","\x0e")]
for name,seq in trials:
    if not proc.isalive(): say(f"{name}: SKIPPED, process dead"); continue
    before=sel()
    try: proc.write(seq)
    except Exception as e: say(f"{name}: write failed {e}"); continue
    time.sleep(1.2)
    after=sel()
    say(f"{name!r:24} alive={proc.isalive()} before={before!r} after={after!r} CHANGED={before!=after}")
say("FINAL selection: "+sel()+f"  alive={proc.isalive()}")
running=False
try: proc.terminate(force=True)
except Exception: pass
LOG.close(); print("done")
