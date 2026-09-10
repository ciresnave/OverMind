import time, threading
import winpty
CWD = r"C:\Users\cires\AppData\Local\Temp\claude\C--Projects-OverMind\1a854350-47ce-4a61-85cf-a890f3dc6c52\scratchpad\puppet"
buf=[]
proc = winpty.PtyProcess.spawn(["claude","--model","haiku"], dimensions=(40,120), cwd=CWD)
running=True
def r():
    while running:
        try: d=proc.read(4096)
        except Exception: break
        if d: buf.append(d)
        else: time.sleep(0.02)
threading.Thread(target=r,daemon=True).start()
time.sleep(20)
running=False
try: proc.terminate(force=True)
except Exception: pass
raw="".join(buf)
open(r"C:\Projects\OverMind\probe\raw.txt","w",encoding="utf-8").write(raw)
esc = raw.replace("\x1b","<ESC>")
import re
modes = sorted(set(re.findall(r"<ESC>\[\?(\d+(?:;\d+)*)([hl])", esc)))
print("len", len(raw))
print("private modes set/reset:", modes)
print("DECCKM(1) set:", "<ESC>[?1h" in esc, " reset:", "<ESC>[?1l" in esc)
print("bracketed paste(2004):", "<ESC>[?2004h" in esc)
print("altscreen(1049):", "<ESC>[?1049h" in esc)
