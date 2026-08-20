/* Render the ```mermaid fences in the write-ups.
 *
 * Mermaid is 2.5 MB, so it is fetched only when a page actually has a diagram
 * on it — which is one page — and only once that diagram is near the viewport.
 * Every fence's source is kept, so a failed render (or a reader with JavaScript
 * off) still gets the plain-text version the write-up was written to be
 * readable as, and a theme flip can re-render from it.
 */

const diagrams = [...document.querySelectorAll("pre > code.language-mermaid")]
  .map((code, i) => ({ host: code.parentElement, source: code.textContent, id: i }));

if (diagrams.length) {
  const dark = () => (document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches);

  let loading = null;
  const load = () => {
    if (globalThis.mermaid) return Promise.resolve(globalThis.mermaid);
    loading ??= new Promise((resolve, reject) => {
      const tag = document.createElement("script");
      tag.src = new URL("vendor/mermaid.min.js", import.meta.url);
      tag.onload = () => resolve(globalThis.mermaid);
      tag.onerror = () => reject(new Error("mermaid failed to load"));
      document.head.append(tag);
    });
    return loading;
  };

  let generation = 0;

  async function render() {
    const mine = ++generation;
    let mermaid;
    try { mermaid = await load(); } catch { return; }
    if (mine !== generation) return;         // a theme flip overtook this pass

    const theme = dark() ? "dark" : "default";
    mermaid.initialize({
      startOnLoad: false,
      theme,
      securityLevel: "strict",
      flowchart: { htmlLabels: true, curve: "basis" },
      fontFamily: getComputedStyle(document.body).fontFamily,
    });

    for (const d of diagrams) {
      try {
        // the id has to change between passes or mermaid reuses the old defs
        const { svg } = await mermaid.render(`mermaid-${d.id}-${mine}`, d.source);
        if (mine !== generation) return;
        const figure = document.createElement("figure");
        figure.className = "figure mermaid";
        figure.innerHTML = svg;
        d.host.replaceWith(figure);
        d.host = figure;
      } catch {
        /* leave this one as a fence — its source is the fallback */
      }
    }
  }

  const observer = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) { observer.disconnect(); render(); }
  }, { rootMargin: "600px" });
  diagrams.forEach((d) => observer.observe(d.host));

  // only re-render once something has been drawn; before that the observer
  // will pick the right theme up on its own
  const reflow = () => { if (globalThis.mermaid) render(); };
  document.addEventListener("themechange", reflow);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", reflow);
}
