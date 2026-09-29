const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const MATH_OPTIONS = {
  delimiters: [
    { left: "$$", right: "$$", display: true },
    { left: "$", right: "$", display: false },
    { left: "\\(", right: "\\)", display: false },
    { left: "\\[", right: "\\]", display: true },
  ],
  throwOnError: false,
  errorColor: "inherit",
  trust: false,
};
const renderedMath = new WeakSet();

export const weightedScore = paper => 2 * Number(paper.RELEVANCE || 0) + Number(paper.NOVELTY || 0);
export const paperId = (paper, fallback = "") => String(paper.arxiv_id || paper.ARXIVID || fallback);
export const visibleAffiliationCount = (references, authorCount) => Math.max(0, ...references.slice(0, authorCount).flat());

export function renderMath(node) {
  const source = node.textContent;
  const hasMath = source.includes("$") || source.includes("\\(") || source.includes("\\[");
  if (renderedMath.has(node) || !hasMath || typeof globalThis.renderMathInElement !== "function") return;
  node.textContent = source.replaceAll("\\$", "\\textdollar{}");
  try {
    globalThis.renderMathInElement(node, MATH_OPTIONS);
  } catch (error) {
    node.textContent = source;
    console.error("Could not render math", error);
    return;
  }
  for (const child of node.childNodes) {
    if (child.nodeType === 3) child.textContent = child.textContent.replaceAll("\\textdollar{}", "$");
  }
  renderedMath.add(node);
}

export function mergeReports(reports) {
  const merged = new Map();
  for (const { date, data } of reports) {
    for (const [key, value] of Object.entries(data)) {
      const candidate = { ...value, arxiv_id: paperId(value, key), date };
      const current = merged.get(candidate.arxiv_id);
      if (!current || candidate.date > current.date ||
          (candidate.date === current.date && Number(candidate.SCORE || 0) > Number(current.SCORE || 0))) {
        merged.set(candidate.arxiv_id, candidate);
      }
    }
  }
  return [...merged.values()];
}

export function sortPapers(papers, sort = "weighted") {
  const score = {
    weighted: weightedScore,
    relevance: paper => Number(paper.RELEVANCE || 0),
    novelty: paper => Number(paper.NOVELTY || 0),
    date: paper => Number(String(paper.date || "").replaceAll("-", "")),
  }[sort] || weightedScore;
  return [...papers].sort((a, b) => score(b) - score(a) ||
    Number(b.RELEVANCE || 0) - Number(a.RELEVANCE || 0) ||
    String(b.date).localeCompare(String(a.date)) || paperId(a).localeCompare(paperId(b)));
}

export function filterPapers(papers, query, minimumRelevance, minimumNovelty) {
  const needle = query.trim().toLocaleLowerCase();
  return papers.filter(paper => {
    const text = [paper.title, ...(paper.authors || []), paper.abstract, paper.COMMENT]
      .filter(Boolean).join(" ").toLocaleLowerCase();
    return Number(paper.RELEVANCE || 0) >= minimumRelevance &&
      Number(paper.NOVELTY || 0) >= minimumNovelty && (!needle || text.includes(needle));
  });
}

function element(tag, attributes = {}, text) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (name === "className") node.className = value;
    else node.setAttribute(name, value);
  }
  if (text !== undefined) node.textContent = text;
  return node;
}

function buildDateTree(entries, selectedDates) {
  const root = document.querySelector("#date-tree");
  root.replaceChildren();
  const years = new Map();
  for (const entry of entries) {
    const [year, month] = entry.date.split("-");
    if (!years.has(year)) years.set(year, new Map());
    const months = years.get(year);
    if (!months.has(month)) months.set(month, []);
    months.get(month).push(entry);
  }
  const branch = (label, content, expanded) => {
    const wrapper = element("div");
    const button = element("button", { type: "button", "aria-expanded": String(expanded) }, label);
    content.hidden = !expanded;
    button.addEventListener("click", () => {
      const open = button.getAttribute("aria-expanded") !== "true";
      button.setAttribute("aria-expanded", String(open));
      content.hidden = !open;
    });
    wrapper.append(button, content);
    return wrapper;
  };
  for (const [year, months] of years) {
    const monthList = element("div", { className: "tree-level" });
    for (const [month, dates] of months) {
      const days = element("div", { className: "tree-level days" });
      for (const entry of dates) {
        const link = element("a", { href: `?date=${entry.date}`, "aria-label": entry.date }, entry.date.split("-")[2]);
        link.addEventListener("click", event => {
          event.preventDefault();
          document.querySelector("#from-date").value = entry.date;
          document.querySelector("#to-date").value = entry.date;
          loadEntries([entry], new URLSearchParams({ date: entry.date }));
        });
        if (selectedDates.has(entry.date)) {
          link.classList.add("selected");
          link.setAttribute("aria-current", "page");
        }
        days.append(link);
      }
      monthList.append(branch(`${month} - ${MONTHS[Number(month) - 1]}`, days,
        dates.some(entry => selectedDates.has(entry.date))));
    }
    root.append(branch(year, monthList,
      [...months.values()].flat().some(entry => selectedDates.has(entry.date))));
  }
}

const state = { entries: [], papers: [], selectedDates: new Set() };
const status = message => document.querySelector("#status").textContent = message;

function render() {
  const query = document.querySelector("#search").value;
  const relevance = Number(document.querySelector("#min-relevance").value || 0);
  const novelty = Number(document.querySelector("#min-novelty").value || 0);
  const sort = document.querySelector("#sort-by").value;
  const papers = sortPapers(filterPapers(state.papers, query, relevance, novelty), sort);
  const container = document.querySelector("#papers");
  const fragment = document.createDocumentFragment();
  status(papers.length ? `${papers.length} paper${papers.length === 1 ? "" : "s"}` : "No papers match these filters.");

  for (const paper of papers) {
    const article = element("article", { className: "paper" });
    const heading = element("h2");
    const link = element("a", { href: `https://arxiv.org/abs/${encodeURIComponent(paperId(paper))}` }, paper.title || paperId(paper));
    renderMath(link);
    heading.append(link);
    article.append(heading);
    const authors = paper.authors || [];
    const references = paper.author_affiliations || [];
    const authorLine = element("p", { className: "paper-meta authors" });
    const authorText = element("span");
    const affiliationList = paper.affiliations?.length && element("ol", { className: "affiliations", "aria-label": "Author affiliations" });
    const renderAuthors = visibleAuthors => {
      authorText.replaceChildren();
      visibleAuthors.forEach((author, index) => {
        if (index) authorText.append(", ");
        authorText.append(author);
        const numbers = references[index] || [];
        if (numbers.length) authorText.append(element("sup", { className: "affiliation-reference" }, numbers.join(",")));
      });
      if (affiliationList) affiliationList.replaceChildren(...paper.affiliations.slice(0, visibleAffiliationCount(references, visibleAuthors.length)).map(affiliation => element("li", {}, affiliation)));
    };
    renderAuthors(authors.slice(0, 10));
    authorLine.append(authorText);
    if (authors.length > 10) {
      const toggle = element("button", { type: "button", className: "author-toggle", "aria-expanded": "false" }, `and ${authors.length - 10} more`);
      toggle.addEventListener("click", () => {
        const expanded = toggle.getAttribute("aria-expanded") === "true";
        toggle.setAttribute("aria-expanded", String(!expanded));
        toggle.textContent = expanded ? `and ${authors.length - 10} more` : "show fewer";
        renderAuthors(expanded ? authors.slice(0, 10) : authors);
      });
      authorLine.append(", ", toggle);
    }
    authorLine.append(` · ${paper.date}`);
    article.append(authorLine);
    if (affiliationList) article.append(affiliationList);
    const badges = element("div", { className: "badges", "aria-label": "Paper scores" });
    badges.append(
      element("span", {}, `Relevance ${Number(paper.RELEVANCE || 0)}`),
      element("span", {}, `Novelty ${Number(paper.NOVELTY || 0)}`),
      element("span", {}, `Weighted ${weightedScore(paper)}`),
    );
    const abstract = element("p", {}, paper.abstract || "");
    renderMath(abstract);
    article.append(badges, abstract);
    if (paper.COMMENT) article.append(element("p", { className: "comment" }, paper.COMMENT));
    fragment.append(article);
  }
  container.replaceChildren(fragment);
}

function entriesInRange(from, to) {
  return state.entries.filter(entry => entry.date >= from && entry.date <= to);
}

async function fetchReports(entries) {
  // Small batches bound simultaneous requests while keeping long ranges responsive.
  const reports = [];
  for (let index = 0; index < entries.length; index += 6) {
    const batch = entries.slice(index, index + 6);
    const fetched = await Promise.all(batch.map(async entry => {
      const response = await fetch(entry.path);
      if (!response.ok) throw new Error(`Could not load ${entry.date} (${response.status})`);
      return { date: entry.date, data: await response.json() };
    }));
    reports.push(...fetched);
  }
  return reports;
}

async function loadEntries(entries, params, replaceHistory = false) {
  if (!entries.length) {
    status("No reports are available in that date range.");
    return;
  }
  status(`Loading ${entries.length} report${entries.length === 1 ? "" : "s"}…`);
  try {
    const reports = await fetchReports(entries);
    state.selectedDates = new Set(entries.map(entry => entry.date));
    state.papers = mergeReports(reports);
    buildDateTree(state.entries, state.selectedDates);
    document.querySelector("#selection-summary").textContent = entries.length === 1
      ? `Recommendations for ${entries[0].date}`
      : `Recommendations from ${entries.at(-1).date} through ${entries[0].date}`;
    history[replaceHistory ? "replaceState" : "pushState"](null, "", `${location.pathname}?${params}`);
    render();
    closeDrawer();
  } catch (error) {
    status(`Unable to load reports: ${error.message}`);
  }
}

function openDrawer() {
  document.body.classList.add("drawer-open");
  document.querySelector("#drawer-button").setAttribute("aria-expanded", "true");
  document.querySelector("#backdrop").hidden = false;
  document.querySelector("#drawer-close").focus();
}

function closeDrawer() {
  document.body.classList.remove("drawer-open");
  document.querySelector("#drawer-button").setAttribute("aria-expanded", "false");
  document.querySelector("#backdrop").hidden = true;
}

async function initialize() {
  try {
    const response = await fetch("manifest.json");
    if (!response.ok) throw new Error(`Could not load archive (${response.status})`);
    const manifest = await response.json();
    state.entries = manifest.dates;
    const available = new Set(state.entries.map(entry => entry.date));
    const params = new URLSearchParams(location.search);
    const from = params.get("from");
    const to = params.get("to");
    const requested = params.get("date");
    let entries;
    let urlParams;
    if (from && to) {
      document.querySelector("#from-date").value = from;
      document.querySelector("#to-date").value = to;
      entries = entriesInRange(from, to);
      urlParams = new URLSearchParams({ from, to });
    } else {
      const selected = available.has(requested) ? requested : manifest.latest;
      document.querySelector("#from-date").value = selected;
      document.querySelector("#to-date").value = selected;
      entries = state.entries.filter(entry => entry.date === selected);
      urlParams = new URLSearchParams({ date: selected });
    }
    await loadEntries(entries, urlParams, true);
  } catch (error) {
    status(`Unable to start the archive: ${error.message}`);
  }
}

if (typeof document !== "undefined") {
  const theme = document.querySelector("#theme");
  const systemTheme = matchMedia("(prefers-color-scheme: dark)");
  const applyTheme = value => {
    document.documentElement.dataset.theme = value === "system" ? (systemTheme.matches ? "dark" : "light") : value;
  };
  const savedTheme = localStorage.getItem("theme") || "system";
  theme.value = savedTheme;
  applyTheme(savedTheme);
  theme.addEventListener("change", () => {
    localStorage.setItem("theme", theme.value);
    applyTheme(theme.value);
  });
  systemTheme.addEventListener("change", () => { if (theme.value === "system") applyTheme("system"); });

  const displaySize = document.querySelector("#display-size");
  const savedDisplaySize = localStorage.getItem("display-size") || "comfortable";
  displaySize.value = savedDisplaySize;
  document.documentElement.dataset.displaySize = savedDisplaySize;
  displaySize.addEventListener("change", () => {
    document.documentElement.dataset.displaySize = displaySize.value;
    localStorage.setItem("display-size", displaySize.value);
  });
  document.querySelector("#range-form").addEventListener("submit", event => {
    event.preventDefault();
    const from = document.querySelector("#from-date").value;
    const to = document.querySelector("#to-date").value;
    if (from > to) {
      status("Start date must be on or before end date.");
      return;
    }
    loadEntries(entriesInRange(from, to), new URLSearchParams({ from, to }));
  });
  for (const selector of ["#search", "#min-relevance", "#min-novelty", "#sort-by"]) {
    document.querySelector(selector).addEventListener("input", render);
  }
  document.querySelector("#drawer-button").addEventListener("click", openDrawer);
  document.querySelector("#drawer-close").addEventListener("click", closeDrawer);
  document.querySelector("#backdrop").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", event => { if (event.key === "Escape") closeDrawer(); });
  window.addEventListener("popstate", () => location.reload());
  initialize();
}
