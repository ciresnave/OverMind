import sys, time, threading
import winpty, pyte

COLS, ROWS = 120, 40
CWD = r"C:\Users\cires\AppData\Local\Temp\claude\C--Projects-OverMind\1a854350-47ce-4a61-85cf-a890f3dc6c52\scratchpad\puppet"
LOG = open(r"C:\Projects\OverMind\probe\screens.log", "w", encoding="utf-8")

screen = pyte.Screen(COLS, ROWS)
stream = pyte.Stream(screen)
lock = threading.Lock()
alive = True
nbytes = [0]

proc = winpty.PtyProcess.spawn(["claude", "--model", "haiku"], dimensions=(ROWS, COLS), cwd=CWD)

def reader():
    while alive:
        try:
            data = proc.read(4096)
        except Exception:
            break
        if data:
            nbytes[0] += len(data)
            with lock:
                stream.feed(data)
        else:
            time.sleep(0.02)

threading.Thread(target=reader, daemon=True).start()

def render():
    with lock:
        return "\n".join(r.rstrip() for r in screen.display)

def dump(tag):
    LOG.write(f"\n{'='*18} {tag} alive={proc.isalive()} pid={proc.pid} bytes={nbytes[0]} {'='*18}\n")
    LOG.write(render() + "\n")
    LOG.flush()

for i in range(6):
    time.sleep(4)
    dump(f"T+{(i+1)*4}s")

alive = False
try: proc.terminate(force=True)
except Exception: pass
LOG.write(f"\nFINAL alive={proc.isalive()}\n")
LOG.close()
print("done")
