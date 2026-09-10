import time, threading
import winpty, pyte
CWD = r"C:\Users\cires\AppData\Local\Temp\claude\C--Projects-OverMind\1a854350-47ce-4a61-85cf-a890f3dc6c52\scratchpad\puppet"
LOG=open(r"C:\Projects\OverMind\probe\ready.log","w",encoding="utf-8")
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
t0=time.time()
while time.time()-t0<60:
    if "trust this folder" in render(): break
    time.sleep(0.15)
proc.write("\x1bOB"); time.sleep(0.5); proc.write("\r")
say(f"[accepted trust at {time.time()-t0:.1f}s]")
for i in range(7):
    time.sleep(3)
    say(f"\n===== T+{(i+1)*3}s after trust  alive={proc.isalive()} bytes={nb[0]} =====")
    say(render())
running=False
try: proc.terminate(force=True)
except Exception: pass
LOG.close(); print("done")
