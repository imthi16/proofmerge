"""The app's Markdown for PR descriptions (untrusted text), run in Node against a fake h()."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MARKDOWN = Path(__file__).parents[1] / "src/proofmerge/dashboard_static/legacy/markdown.js"
NODE = shutil.which("node")
# Prints the element tree as compact tags, with href for links.
SCRIPT = """
const md = require(process.argv[1]);
const h = (t, p, ...c) => ({ t, p: p || {}, c: c.filter((x) => x !== null) });
const show = (n) => typeof n === "string" ? n
  : `<${n.t}${n.p.href ? ` href=${n.p.href}` : ""}${n.p.disabled ? " disabled" : ""}>${n.c.map(show).join("")}</${n.t}>`;
let text = ""; process.stdin.on("data", (d) => (text += d));
process.stdin.on("end", () => console.log(JSON.stringify(md(h, text).map(show))));
"""


def render(text: str) -> list[str]:
    result = subprocess.run([NODE, "-e", SCRIPT, str(MARKDOWN)], input=text, capture_output=True,
                            text=True, timeout=30, check=True)
    return json.loads(result.stdout)


pytestmark = pytest.mark.skipif(NODE is None, reason="needs node")


def test_github_markdown_becomes_elements() -> None:
    out = render("## Why\nCache **prices** in `price_for`.\nSecond line\n\n- [x] tests\n- docs\n1. one\n\n"
                 "> note\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```\ncode <b>\n```\n---")
    assert out[0] == "<h4>Why</h4>"
    assert out[1] == "<p>Cache <strong>prices</strong> in <code>price_for</code>.<br></br>Second line</p>"
    assert out[2] == "<ul><li><input disabled></input>tests</li><li>docs</li></ul>"
    assert out[3] == "<ol><li>one</li></ol>"
    assert out[4] == "<blockquote><p>note</p></blockquote>"
    assert out[5].startswith("<div><table><thead><tr><th>a</th>") and "<td>2</td>" in out[5]
    assert out[6] == "<pre><code>code <b></code></pre>"  # text inside an element, never parsed as HTML
    assert out[7] == "<hr></hr>"


def test_untrusted_links_images_and_html_stay_inert() -> None:
    out = render("[x](javascript:alert(1)) [y](data:text/html,hi) ![pic](https://t.example/p.png) "
                 "<img src=x onerror=alert(1)> https://ok.example/a.\n<!-- hidden note -->")
    (para,) = out
    assert "href=javascript" not in para and "href=data" not in para
    assert "<a href=https://t.example/p.png>image: pic</a>" in para  # a link, never a loaded <img>
    assert "<img src=x onerror=alert(1)>" in para  # plain text; React escapes it
    assert "<a href=https://ok.example/a>https://ok.example/a</a>." in para
    assert "hidden note" not in para
