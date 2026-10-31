#!/usr/bin/python3
"""Publish this Weston's private display environment and notify systemd once."""
import os
import re
import subprocess
from pathlib import Path

runtime = Path(os.environ['XDG_RUNTIME_DIR']) / 'valkyrie'
display = os.environ['DISPLAY']
if not re.fullmatch(r':\d+', display):
    raise SystemExit(f'Unexpected Xwayland display: {display!r}')
# Activate Weston's lazy Xwayland and wait for its first successful connection.
subprocess.run(['/usr/bin/xprop', '-root'], stdout=subprocess.DEVNULL,
               check=True, timeout=15)
path = runtime / 'display.env'
temporary = path.with_suffix('.tmp')
temporary.write_text(f'DISPLAY={display}\nWAYLAND_DISPLAY={runtime}/wayland-0\nWESTON_PID={os.getppid()}\n')
temporary.chmod(0o600)
temporary.replace(path)
subprocess.run(['/usr/bin/systemd-notify', '--ready',
                '--status=AMD renderer and private Xwayland ready'], check=True)
