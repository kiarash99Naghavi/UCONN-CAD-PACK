/* Task page: three locked viewers, plus an overlay that puts our edit and the
 * expert's in one scene.
 *
 * The overlay is the view that actually answers the question the benchmark
 * asks. Diff F1 scores the voxels each edit *changed*, so two parts that look
 * the same from outside can score zero against each other; dropping both into
 * one frame at 55% opacity is the fastest way to see whether we changed the
 * same material the human did, or different material that happens to look
 * plausible.
 */

import { mount, linkCameras, retheme, ROLE_COLOR } from "./viewer.js";

const data = JSON.parse(document.querySelector("#task-data").textContent);

const LABELS = { input: "Input part", ours: "Our edit", gt: "Expert edit" };
const roles = ["input", "ours", "gt"].filter((r) => data.meshes[r]);

const row = document.querySelector("#viewers");
const bar = document.querySelector("#viewer-bar");
const viewers = [];

function shell(role) {
  const box = document.createElement("div");
  box.className = "viewer";
  box.innerHTML = `
    <div class="stage"></div>
    <div class="cap"><span class="dot dot-${role}"></span>${LABELS[role]}
      <span class="tris"></span></div>
    <div class="state">loading…</div>`;
  row.append(box);
  return box;
}

function failed(box, message) {
  const state = box.querySelector(".state");
  state.hidden = false;
  state.textContent = message;
}

async function buildSideBySide() {
  row.className = "viewer-row";
  row.textContent = "";
  viewers.length = 0;

  for (const role of roles) {
    const box = shell(role);
    try {
      const viewer = await mount(box.querySelector(".stage"),
        [{ url: data.meshes[role], color: ROLE_COLOR[role] }]);
      box.querySelector(".state").hidden = true;
      box.querySelector(".tris").textContent =
        viewer.triangles.toLocaleString() + " tris";
      viewers.push(viewer);
    } catch {
      failed(box, "could not load this part");
    }
  }
  linkCameras(viewers);
}

async function buildOverlay() {
  row.className = "viewer-row single";
  row.textContent = "";
  viewers.length = 0;

  const box = document.createElement("div");
  box.className = "viewer";
  box.innerHTML = `
    <div class="stage"></div>
    <div class="cap"><span class="dot dot-ours"></span>Our edit
      <span class="dot dot-gt" style="margin-left:.6rem"></span>Expert edit</div>
    <div class="state">loading…</div>`;
  row.append(box);

  try {
    const viewer = await mount(box.querySelector(".stage"), [
      { url: data.meshes.ours, color: ROLE_COLOR.ours, opacity: 0.55, edges: false },
      { url: data.meshes.gt, color: ROLE_COLOR.gt, opacity: 0.55, edges: false },
    ]);
    box.querySelector(".state").hidden = true;
    viewers.push(viewer);
  } catch {
    failed(box, "could not load these parts");
  }
}

/* ---------- controls ---------- */

function toggle(label, pressed, onChange, dots = []) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "chip";
  button.setAttribute("aria-pressed", String(pressed));
  button.innerHTML = dots.map((d) => `<span class="dot dot-${d}"></span>`).join("") + label;
  button.addEventListener("click", () => {
    const next = button.getAttribute("aria-pressed") !== "true";
    button.setAttribute("aria-pressed", String(next));
    onChange(next);
  });
  bar.append(button);
  return button;
}

let overlay = false;
let edges = true;
let spin = false;

async function rebuild() {
  await (overlay ? buildOverlay() : buildSideBySide());
  for (const v of viewers) { v.setEdgesVisible(edges); v.setSpinning(spin); }
}

if (data.meshes.ours && data.meshes.gt) {
  toggle("Overlay ours + expert", false, async (on) => { overlay = on; await rebuild(); },
    ["ours", "gt"]);
}
toggle("Edges", true, (on) => { edges = on; viewers.forEach((v) => v.setEdgesVisible(on)); });
toggle("Spin", false, (on) => { spin = on; viewers.forEach((v) => v.setSpinning(on)); });

const reset = document.createElement("button");
reset.type = "button";
reset.className = "chip";
reset.textContent = "Reset view";
reset.addEventListener("click", () => viewers.forEach((v) => v.home()));
bar.append(reset);

const hint = document.createElement("span");
hint.className = "hint";
hint.textContent = "drag to orbit · scroll to zoom · cameras are locked together";
bar.append(hint);

document.addEventListener("themechange", retheme);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", retheme);

rebuild();
