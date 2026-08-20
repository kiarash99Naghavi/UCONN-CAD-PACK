/* The landing page's live part.
 *
 * Mounted lazily: the hero GLB is a few hundred kilobytes and there is no
 * reason to spend that on a visitor who lands below the fold or bounces. The
 * observer fires once, then disconnects.
 */

import { mount, ROLE_COLOR } from "./viewer.js";

const host = document.querySelector("#hero-viewer");
if (host) {
  const url = host.dataset.mesh;
  const stage = host.querySelector(".stage");
  const state = host.querySelector(".state");

  const start = async () => {
    try {
      const viewer = await mount(stage, [{ url, color: ROLE_COLOR.ours }]);
      state.hidden = true;
      host.querySelector(".tris").textContent =
        viewer.triangles.toLocaleString() + " tris";
      viewer.setSpinning(!matchMedia("(prefers-reduced-motion: reduce)").matches);
      // spinning is an invitation, not a mode — the first touch hands the
      // camera over to the reader and it stays theirs
      const stop = () => viewer.setSpinning(false);
      stage.addEventListener("pointerdown", stop, { once: true });
      stage.addEventListener("wheel", stop, { once: true, passive: true });
    } catch {
      state.textContent = "could not load this part";
    }
  };

  const observer = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) { observer.disconnect(); start(); }
  }, { rootMargin: "200px" });
  observer.observe(host);
}
