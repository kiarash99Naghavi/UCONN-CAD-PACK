/* Gallery filtering and sorting.
 *
 * The 48 cards are rendered by the build, not by this file — the gallery is
 * readable with JavaScript off and does not flash empty on load. All this does
 * is hide, show and reorder what is already in the DOM, reading the numbers off
 * the data attributes the build wrote onto each card.
 */

const grid = document.querySelector("#gallery");
if (grid) {
  const cards = [...grid.children];
  const count = document.querySelector("#count");
  const empty = document.querySelector("#empty");
  const search = document.querySelector("#search");
  const sortBy = document.querySelector("#sort");
  const difficultyButtons = [...document.querySelectorAll("#difficulty button")];

  const state = { difficulty: "all", query: "", sort: "diff_f1-desc" };

  const number = (card, key) => parseFloat(card.dataset[key]);

  function apply() {
    const q = state.query.trim().toLowerCase();
    let shown = 0;
    for (const card of cards) {
      const ok =
        (state.difficulty === "all" || card.dataset.difficulty === state.difficulty) &&
        (!q || card.dataset.search.includes(q));
      card.hidden = !ok;
      if (ok) shown++;
    }

    const [key, direction] = state.sort.split("-");
    const sign = direction === "desc" ? -1 : 1;
    const order = [...cards].sort((a, b) => {
      if (key === "difficulty") {
        const rank = { easy: 0, medium: 1, hard: 2 };
        const d = rank[a.dataset.difficulty] - rank[b.dataset.difficulty];
        return d !== 0 ? sign * d : number(b, "diffF1") - number(a, "diffF1");
      }
      return sign * (number(a, key) - number(b, key));
    });
    order.forEach((card) => grid.append(card));

    count.textContent = shown === cards.length
      ? `${cards.length} tasks`
      : `${shown} of ${cards.length} tasks`;
    empty.hidden = shown > 0;
  }

  for (const button of difficultyButtons) {
    button.addEventListener("click", () => {
      state.difficulty = button.dataset.value;
      for (const other of difficultyButtons) {
        other.setAttribute("aria-pressed", String(other === button));
      }
      apply();
    });
  }
  search?.addEventListener("input", () => { state.query = search.value; apply(); });
  sortBy?.addEventListener("change", () => { state.sort = sortBy.value; apply(); });

  apply();
}
