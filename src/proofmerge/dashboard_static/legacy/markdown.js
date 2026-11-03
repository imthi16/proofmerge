/* Markdown for pull request descriptions, as GitHub writes them. The text is untrusted, so it
   becomes elements through h (React.createElement), never HTML: tags in the text show as text,
   links open only http(s) addresses, and images are not loaded (they show as links). */
(function (root) {
  "use strict";
  const SAFE_URL = /^https?:\/\//i;
  // Every span has a bounded length, so a hostile description (thousands of backticks or
  // asterisks) costs linear time instead of freezing the page while it renders.
  const INLINE = /(`{1,3})([^`]{1,1000}?)\1(?!`)|(!?)\[([^\]]{0,500})\]\(\s*<?([^)\s>]{1,2000})>?(?:\s+"[^"]{0,200}")?\s*\)|(\*\*|__)(?=\S)([\s\S]{0,1000}?\S)\6|~~(?=\S)([\s\S]{0,1000}?\S)~~|(\*|_)(?=[^\s*_])([\s\S]{0,1000}?[^\s*_])\9(?![A-Za-z0-9])|(https?:\/\/[^\s<>()]{1,2000}[^\s<>().,;:!?'"])/g;

  function inline(h, text, key) {
    const out = [];
    let last = 0, n = 0, m;
    const re = new RegExp(INLINE.source, "g");
    while ((m = re.exec(text))) {
      if (m.index > last) out.push(text.slice(last, m.index));
      const k = `${key}.${n++}`;
      if (m[1]) out.push(h("code", { key: k }, m[2].trim()));
      else if (m[5] !== undefined) {
        const label = m[3] ? (m[4] ? `image: ${m[4]}` : "image") : m[4] || m[5];
        out.push(SAFE_URL.test(m[5])
          ? h("a", { key: k, href: m[5], target: "_blank", rel: "noopener noreferrer" }, ...inline(h, label, k))
          : h("span", { key: k }, ...inline(h, label, k)));
      } else if (m[6]) out.push(h("strong", { key: k }, ...inline(h, m[7], k)));
      else if (m[8] !== undefined) out.push(h("del", { key: k }, ...inline(h, m[8], k)));
      else if (m[9]) out.push(h("em", { key: k }, ...inline(h, m[10], k)));
      else if (m[11]) out.push(h("a", { key: k, href: m[11], target: "_blank", rel: "noopener noreferrer" }, m[11]));
      last = re.lastIndex;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }

  // GitHub keeps single line breaks inside a paragraph, so they stay breaks here.
  function lines(h, rows, key) {
    return rows.flatMap((row, i) => (i ? [h("br", { key: `${key}.br${i}` })] : []).concat(inline(h, row, `${key}.${i}`)));
  }

  const LIST = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
  const FENCE = /^\s*(`{3,}|~{3,})/;
  const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
  const TABLE_SEP = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/;
  const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

  function blocks(h, rows, key, depth = 0) {
    const out = [];
    let i = 0;
    const k = () => `${key}.${out.length}`;
    while (i < rows.length) {
      const row = rows[i];
      if (!row.trim()) { i++; continue; }
      const fence = row.match(FENCE);
      if (fence) {
        const body = [];
        for (i++; i < rows.length && !rows[i].trim().startsWith(fence[1]); i++) body.push(rows[i]);
        i++;
        out.push(h("pre", { key: k() }, h("code", null, body.join("\n"))));
        continue;
      }
      const head = row.match(/^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/);
      if (head) {
        // Description headings sit under the panel's own heading, so they start at h3.
        out.push(h("h" + Math.min(6, head[1].length + 2), { key: k() }, ...inline(h, head[2], k())));
        i++; continue;
      }
      if (RULE.test(row)) { out.push(h("hr", { key: k() })); i++; continue; }
      if (/^\s{0,3}>/.test(row)) {
        const quoted = [];
        for (; i < rows.length && /^\s{0,3}>/.test(rows[i]); i++) quoted.push(rows[i].replace(/^\s{0,3}>\s?/, ""));
        // Nesting is capped: thousands of ">" would otherwise overflow the call stack.
        out.push(h("blockquote", { key: k() }, ...(depth < 8 ? blocks(h, quoted, k(), depth + 1)
          : [h("p", { key: "deep" }, quoted.join(" "))])));
        continue;
      }
      if (row.includes("|") && i + 1 < rows.length && TABLE_SEP.test(rows[i + 1]) && rows[i + 1].includes("-")) {
        const top = cells(row);
        const body = [];
        for (i += 2; i < rows.length && rows[i].includes("|") && rows[i].trim(); i++) body.push(cells(rows[i]));
        const tk = k();
        out.push(h("div", { key: tk, className: "md-table" }, h("table", null,
          h("thead", null, h("tr", null, ...top.map((c, j) => h("th", { key: j }, ...inline(h, c, `${tk}.h${j}`))))),
          h("tbody", null, ...body.map((r, y) => h("tr", { key: y }, ...r.map((c, j) => h("td", { key: j }, ...inline(h, c, `${tk}.${y}.${j}`)))))))));
        continue;
      }
      const item = row.match(LIST);
      if (item) {
        const ordered = /\d/.test(item[2]);
        const entries = [];
        for (; i < rows.length; i++) {
          const m = rows[i].match(LIST);
          if (m && !m[1] && /\d/.test(m[2]) !== ordered) break; // "1." after "-" starts a new list
          if (m) entries.push({ depth: Math.min(3, Math.floor(m[1].replace(/\t/g, "    ").length / 2)), text: [m[3]] });
          else if (rows[i].trim() && entries.length && /^\s+\S/.test(rows[i])) entries[entries.length - 1].text.push(rows[i].trim());
          else break;
        }
        const lk = k();
        out.push(h(ordered ? "ol" : "ul", { key: lk, className: "md-list" }, ...entries.map((e, j) => {
          const task = e.text[0].match(/^\[([ xX])\]\s+(.*)$/);
          const text = task ? [task[2], ...e.text.slice(1)] : e.text;
          return h("li", { key: j, className: (task ? "task " : "") + "d" + e.depth },
            task ? h("input", { type: "checkbox", checked: task[1] !== " ", disabled: true, readOnly: true, "aria-label": task[1] !== " " ? "done" : "not done" }) : null,
            ...lines(h, text, `${lk}.${j}`));
        })));
        continue;
      }
      const para = [];
      for (; i < rows.length && rows[i].trim() && !FENCE.test(rows[i]) && !LIST.test(rows[i]) && !/^\s{0,3}(#{1,6}\s|>)/.test(rows[i]) && !RULE.test(rows[i]); i++) para.push(rows[i].trim());
      if (!para.length) { para.push(rows[i].trim()); i++; }
      out.push(h("p", { key: k() }, ...lines(h, para, k())));
    }
    return out;
  }

  function markdown(h, text) {
    // HTML comments are how PR templates leave notes for the author; GitHub hides them too.
    const clean = String(text || "").replace(/<!--[\s\S]*?-->/g, "").replace(/\r\n?/g, "\n");
    return blocks(h, clean.split("\n"), "md");
  }

  if (typeof module === "object" && module.exports) module.exports = markdown;
  else root.ProofMergeMarkdown = markdown;
})(typeof window === "undefined" ? globalThis : window);
