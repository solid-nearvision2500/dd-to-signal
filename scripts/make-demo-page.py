#!/usr/bin/env python
"""Render the real CLI output into an animatable terminal page.

The README GIF has to show the actual tool, not a mock-up of it, so this runs
the command for real, captures the ANSI it prints and converts that to HTML. If
the output changes, the GIF changes with it, and if the tool breaks the GIF
cannot be recorded at all -- which is the property that keeps a README honest.

    python scripts/make-demo-page.py
    node scripts/record-gif.mjs
"""

from __future__ import annotations

import html
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"

COMMAND = ["dd-to-signal", "demo", "AAPL"]

#: The subset of SGR codes the terminal renderer emits, mapped to CSS classes.
CLASSES = {
    "1": "b",
    "2": "dim",
    "31": "red",
    "32": "green",
    "33": "yellow",
    "34": "blue",
    "36": "cyan",
    "90": "grey",
    "41": "delbg",
    "42": "addbg",
    "97": "white",
    "30": "black",
}

_SGR = re.compile(r"\033\[([0-9;]*)m")


def ansi_to_html(text: str) -> str:
    """Convert the ANSI we emit into spans. Not a general terminal emulator."""
    out: list[str] = []
    open_spans = 0
    position = 0

    for match in _SGR.finditer(text):
        out.append(html.escape(text[position : match.start()]))
        position = match.end()

        codes = [c for c in match.group(1).split(";") if c] or ["0"]
        if codes == ["0"]:
            out.append("</span>" * open_spans)
            open_spans = 0
            continue

        names = " ".join(CLASSES[c] for c in codes if c in CLASSES)
        out.append(f'<span class="{names}">')
        open_spans += 1

    out.append(html.escape(text[position:]))
    out.append("</span>" * open_spans)
    return "".join(out)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>dd-to-signal</title>
<style>
  :root {{
    --bg: #0d1117; --chrome: #161b22; --line: #30363d; --ink: #e6edf3;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; background: #010409; }}
  body {{
    display: flex; align-items: center; justify-content: center;
    height: 100vh; padding: 18px;
    font: 13px/1.55 ui-monospace, "SF Mono", "Cascadia Mono", Consolas, monospace;
  }}
  .term {{
    width: 100%; height: 100%; background: var(--bg); border-radius: 10px;
    border: 1px solid var(--line); overflow: hidden;
    box-shadow: 0 18px 50px rgba(0,0,0,.6);
    display: flex; flex-direction: column;
  }}
  .bar {{
    display: flex; align-items: center; gap: 8px; padding: 9px 13px;
    background: var(--chrome); border-bottom: 1px solid var(--line);
  }}
  .dot {{ width: 11px; height: 11px; border-radius: 50%; }}
  .dot.r {{ background: #ff5f57; }} .dot.y {{ background: #febc2e; }}
  .dot.g {{ background: #28c840; }}
  .bar .title {{
    margin-left: 8px; color: #7d8590; font-size: 11.5px; letter-spacing: .02em;
  }}
  pre {{
    margin: 0; padding: 14px 16px; color: var(--ink); flex: 1;
    white-space: pre-wrap; word-break: break-word;
    overflow-y: auto; scrollbar-width: none;
  }}
  pre::-webkit-scrollbar {{ display: none; }}
  .prompt {{ color: #7ee787; }}
  .path {{ color: #58a6ff; }}
  .cursor {{
    display: inline-block; width: 7.6px; height: 15px; background: #e6edf3;
    vertical-align: -3px;
  }}
  .cursor.off {{ opacity: 0; }}
  .b {{ font-weight: 700; }} .dim {{ opacity: .72; }}
  .red {{ color: #f85149; }} .green {{ color: #3fb950; }}
  .yellow {{ color: #d29922; }} .blue {{ color: #58a6ff; }}
  .cyan {{ color: #39c5cf; }} .grey {{ color: #7d8590; }} .white {{ color: #fff; }}
  .black {{ color: #0d1117; }}
  .delbg {{ background: rgba(248,81,73,.28); color: #ffc1bc; border-radius: 2px; }}
  .addbg {{ background: rgba(63,185,80,.26); color: #b5f2c0; border-radius: 2px; }}
</style></head>
<body>
  <div class="term">
    <div class="bar">
      <span class="dot r"></span><span class="dot y"></span><span class="dot g"></span>
      <span class="title">dd-to-signal &mdash; what changed in Apple's 10-K</span>
    </div>
    <pre id="screen"></pre>
  </div>
<script>
const COMMAND = {command!r};
const OUTPUT = {output};
const screen = document.getElementById('screen');

// Frames before the output starts: type the command, then a short beat.
// Newline as a constant: this template goes through str.format and then into
// an HTML file, and a backslash escape would have to survive both intact.
const NL = String.fromCharCode(10);
const PAUSE = 4;

function frame(step) {{
  const typed = Math.min(step, COMMAND.length);
  const lines = Math.max(0, step - COMMAND.length - PAUSE);
  const cursorOn = typed < COMMAND.length || step % 2 === 0;

  const typedHtml = COMMAND.slice(0, typed)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;');
  const cursor = '<span class="cursor' + (cursorOn ? '' : ' off') + '"></span>';
  const body = OUTPUT.slice(0, lines).join(NL);

  screen.innerHTML =
    '<span class="prompt">$</span> ' + typedHtml + cursor + (body ? NL + body : '');

  // Follow the output the way a real terminal does, so a long excerpt scrolls
  // into view rather than being cut off by the bottom of the window.
  screen.scrollTop = screen.scrollHeight;
}}

window.__frame = frame;
window.__typingEnd = COMMAND.length + PAUSE;
window.__total = COMMAND.length + PAUSE + OUTPUT.length;
frame(0);
</script>
</body></html>
"""


def main() -> int:
    env = {**os.environ, "FORCE_COLOR": "1", "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(COMMAND, capture_output=True, env=env, text=True, encoding="utf-8")
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return result.returncode

    lines = [ansi_to_html(line) for line in result.stdout.rstrip("\n").split("\n")]

    import json

    BUILD.mkdir(exist_ok=True)
    page = PAGE.format(command=" ".join(COMMAND), output=json.dumps(lines))
    path = BUILD / "terminal.html"
    path.write_text(page, encoding="utf-8")
    print(f"  wrote {path}  ({len(lines)} lines of real output)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
