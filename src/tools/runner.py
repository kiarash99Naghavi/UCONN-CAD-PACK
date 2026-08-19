"""Run an agent-generated CadQuery function in an isolated subprocess."""

import json
import os
import os.path as osp
import subprocess
import sys

from .. import config

_EXEC = osp.join(osp.dirname(osp.abspath(__file__)), "exec_script.py")


def run_script(function_src, input_step, out_dir, timeout=None):
    """Execute `function_src`. Returns (ok, info, log_text).

    `info` carries the exported STEP path and coarse geometry stats on success,
    or the error string on failure. `log_text` is the trimmed stdout/stderr that
    gets fed back to the executor agent as its feedback signal.
    """
    timeout = timeout or config.SCRIPT_TIMEOUT_S
    os.makedirs(out_dir, exist_ok=True)
    fn_file = osp.join(out_dir, "candidate_function.py")
    with open(fn_file, "w") as f:
        f.write(function_src)

    cmd = [sys.executable, _EXEC, "--function_file", fn_file,
           "--output_dir", out_dir]
    if input_step:
        cmd += ["--input_file", str(input_step)]

    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out, err = p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return False, {"ok": False, "error": f"timed out after {timeout}s"}, (
            f"TIMEOUT: the script did not finish within {timeout}s. It is "
            f"probably doing something far too expensive (a fillet on every "
            f"edge of a 600-edge solid, or an unbounded loop).")

    info = {"ok": False, "error": "no RESULT line — the script crashed early"}
    for line in out.splitlines():
        if line.startswith("RESULT: "):
            try:
                info = json.loads(line[len("RESULT: "):])
            except json.JSONDecodeError:
                pass

    log = (out + ("\n" + err if err.strip() else "")).strip()
    if len(log) > 6000:                       # keep feedback affordable
        log = log[:3000] + "\n...[trimmed]...\n" + log[-3000:]
    return bool(info.get("ok")), info, log
