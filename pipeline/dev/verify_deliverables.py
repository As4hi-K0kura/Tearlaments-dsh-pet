
# --- 仓库可移植化补丁（由发布脚本注入）---
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import _paths
# --- end ---
import hashlib
import os
import re
import subprocess
import io

FF = _paths.ffmpeg_bin()
DELIV = str(_paths.DELIV)
LIST = os.path.join(DELIV, "清单.md")
FINAL = str(_paths.VID_FINAL)

text = io.open(LIST, encoding="utf-8").read()
rows = re.findall(r"\|\s*`(pet-\w+\.mp4)`\s*\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|", text)
print("%-16s %-10s %-12s %-8s %-6s %-6s" % ("file", "md5", "size", "dur", "res", "audio"))
ok = True
for name, state, kind, dur, size, md5, ver, score in rows:
    p = os.path.join(DELIV, name)
    if not os.path.exists(p):
        print("%-16s MISSING" % name)
        ok = False
        continue
    data = open(p, "rb").read()
    h = hashlib.md5(data).hexdigest().upper()
    size_txt = size.strip()
    exp_size = int(re.findall(r"(\d+)\s*B", size_txt)[0])
    exp_md5 = md5.strip().strip("`")
    out = subprocess.run([FF, "-i", p], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="ignore")
    err = out.stderr
    dur_s = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", err)
    d = "%02d:%02d:%s" % (int(dur_s.group(1)), int(dur_s.group(2)), dur_s.group(3)) if dur_s else "?"
    res = re.search(r"(\d{2,4})x(\d{2,4})", err)
    naudio = err.count("Audio:") == 0
    s_ok = len(data) == exp_size
    m_ok = h == exp_md5
    if not (s_ok and m_ok and naudio):
        ok = False
    print("%-16s %-10s %-12s %-8s %-6s %-6s  %s" % (
        name, "OK" if m_ok else "MISMATCH", "%d%s" % (len(data), "" if s_ok else "(!)"),
        d, res.group(0) if res else "?", "none" if naudio else "HAS!", ver.strip()))
print("\nALL OK" if ok else "\nHAS PROBLEM")
