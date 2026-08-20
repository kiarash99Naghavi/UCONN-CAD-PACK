/* Page shell: in-page table of contents, sortable tables.
 * Loaded on every page; each block no-ops when its markup is absent. */

/* ---------- table of contents ---------- */

const toc = document.querySelector(".toc ul");
if (toc) {
  const headings = [...document.querySelectorAll(".doc-body h2, .doc-body h3")]
    .filter((h) => h.id);
  if (!headings.length) {
    toc.closest(".toc").hidden = true;
  } else {
    for (const h of headings) {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = "#" + h.id;
      a.textContent = h.textContent.replace(/\s*¶\s*$/, "");
      if (h.tagName === "H3") a.className = "h3";
      li.append(a);
      toc.append(li);
    }
    // highlight the heading nearest the top of the viewport, not merely the
    // last one that crossed it — long sections otherwise leave the marker
    // stranded on a heading that scrolled away minutes ago
    const links = new Map(headings.map((h, i) => [h, toc.children[i].firstChild]));
    const seen = new Set();
    const observer = new IntersectionObserver((entries) => {
      for (const e of entries) e.isIntersecting ? seen.add(e.target) : seen.delete(e.target);
      const active = headings.find((h) => seen.has(h));
      for (const [h, a] of links) a.classList.toggle("active", h === active);
    }, { rootMargin: "-72px 0px -70% 0px" });
    headings.forEach((h) => observer.observe(h));
  }
}

/* ---------- sortable tables ---------- */

for (const table of document.querySelectorAll("table[data-sortable]")) {
  const body = table.tBodies[0];
  for (const [column, th] of [...table.tHead.rows[0].cells].entries()) {
    if (!th.classList.contains("sortable")) continue;
    th.tabIndex = 0;
    const sort = () => {
      const descending = th.getAttribute("aria-sort") !== "descending";
      for (const other of table.tHead.rows[0].cells) other.removeAttribute("aria-sort");
      th.setAttribute("aria-sort", descending ? "descending" : "ascending");

      const key = (row) => {
        const cell = row.cells[column];
        const raw = cell.dataset.sort ?? cell.textContent.trim();
        const n = parseFloat(raw);
        return Number.isNaN(n) ? raw.toLowerCase() : n;
      };
      const rows = [...body.rows].sort((a, b) => {
        const x = key(a), y = key(b);
        const cmp = typeof x === "number" && typeof y === "number"
          ? x - y : String(x).localeCompare(String(y));
        return descending ? -cmp : cmp;
      });
      rows.forEach((r) => body.append(r));
    };
    th.addEventListener("click", sort);
    th.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); }
    });
  }
}
