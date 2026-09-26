// cppmanlite — client-side search + manpage-style reader
// Two-panel layout: sidebar (search + results) | main (page content)
// Press Enter to open the top result directly, like `man`.
//
// Search is a small deterministic scorer over the index (no lunr). Rationale:
// lunr's BM25 + tokenizer buried exact-title hits — searching "std::max" never
// returned the real std::max page, it led with RAND_MAX/fmax. A hand-rolled
// scorer (exact title ≫ alias title ≫ identifier in terms ≫ url ≫ snippet,
// whole-token first, substring fallback) puts the right page first and is
// fully predictable. See the test harness notes in scripts/.

let allDocs = [];
let docMeta = [];       // precomputed search fields (built once in init)
let currentQuery = "";
let currentPageDir = ""; // dir of the currently displayed page, for resolving relative links

// ---- tokenization / normalisation (must match cppmanlite/core.py) ----
const IDENT_RE = /[a-z_][a-z0-9_]*|[0-9]+/g;
function norm(s) { return (s || "").toLowerCase(); }
function tokens(s) { const m = norm(s).match(IDENT_RE); return m ? m : []; }
function stripNs(s) { return s.replace(/^(std::)+/, ""); }
function stripArgs(s) { return s.replace(/\(.*\)\s*$/, "").trim(); }

// cppreference-style aliases: map the common name a user types to the
// canonical identifier used in page titles. (std::string == std::basic_string)
const ALIAS = {
  string: "basic_string", wstring: "basic_wstring",
  u8string: "basic_u8string", u16string: "basic_u16string", u32string: "basic_u32string",
  str: "basic_string", wstr: "basic_wstring",
};
function aliasExpand(s) {
  return s.split(/[\s_]+/).filter(Boolean).map((t) => ALIAS[t] || t).join("_");
}

// field weights: whole-token match (full) and substring-inside-larger-token (sub)
const W = { title: [100, 40], terms: [80, 32], url: [40, 16], snippet: [8, 3] };

function buildMeta(docs) {
  return docs.map((d) => {
    // terms is a {identifier: count} map (most frequent first), or [] for older indexes
    const termFreq = (d.terms && typeof d.terms === "object" && !Array.isArray(d.terms))
      ? d.terms : {};
    return {
      title: d.title, url: d.url, snippet: d.snippet,
      tLow: norm(d.title), uLow: d.url.toLowerCase(), sLow: norm(d.snippet),
      tTok: new Set(tokens(d.title)),
      uTok: new Set(tokens(d.url)),
      sTok: new Set(tokens(d.snippet)),
      termFreq,                       // identifier -> occurrence count in body
      termsLow: Object.keys(termFreq).join(" "),
    };
  });
}

function matchTok(tok, str, tokSet, full, sub) {
  if (tokSet.has(tok)) return full;
  if (str.includes(tok)) return sub;
  return 0;
}

// term score: the base "terms" weight, scaled up (log) by how often the
// identifier appears in the page body. A page that defines int64_t (×8)
// outranks one that mentions it once (×1) in an example.
function termScore(tok, termFreq, termsLow) {
  const n = termFreq[tok];
  if (n) return W.terms[0] * (1 + Math.log2(n));
  if (termsLow.includes(tok)) return W.terms[1]; // substring inside a larger token
  return 0;
}

function docScore(meta, searchToks, wholeCands) {
  let score = 0, matched = 0;
  // Whole-title relations: EXACT always; PREFIX only for specific/compound
  // queries so a bare "string" doesn't beat real pages.
  for (const wc of wholeCands) {
    if (!wc || wc.length < 2) continue;
    const dT = stripNs(meta.tLow);
    if (dT === wc) score += 1000;
    else {
      const specific = searchToks.length >= 2 || wc.includes("_") || wc.length >= 8;
      if (specific && dT.startsWith(wc + " ")) score += 400;
    }
  }
  for (const tok of searchToks) {
    const s = Math.max(
      matchTok(tok, meta.tLow, meta.tTok, W.title[0], W.title[1]),
      termScore(tok, meta.termFreq, meta.termsLow),
      matchTok(tok, meta.uLow, meta.uTok, W.url[0], W.url[1]),
      matchTok(tok, meta.sLow, meta.sTok, W.snippet[0], W.snippet[1]),
    );
    if (s > 0) { score += s; matched++; }
  }
  // AND semantics: penalise if not every query token matched somewhere.
  if (searchToks.length > 1 && matched < searchToks.length) score *= matched / searchToks.length;
  return score;
}

function search(query, limit = 30) {
  const qNorm = norm(query).trim();
  if (!qNorm) return [];
  let qToks = tokens(query);
  if (qToks.length && qToks[0] === "std" && qNorm.startsWith("std::")) qToks = qToks.slice(1);
  if (!qToks.length) return [];
  const searchToks = [...new Set([...qToks, ...qToks.map((t) => ALIAS[t] || "").filter(Boolean)])];
  const qTitleWhole = stripArgs(stripNs(qNorm));
  const wholeCands = [...new Set([qTitleWhole, aliasExpand(qTitleWhole)])].filter((s) => s && s.length >= 2);
  const scored = [];
  for (let i = 0; i < docMeta.length; i++) {
    const score = docScore(docMeta[i], searchToks, wholeCands);
    if (score > 0) scored.push({ ...allDocs[i], score });
  }
  scored.sort((a, b) => b.score - a.score || norm(a.title).length - norm(b.title).length || a.url.localeCompare(b.url));
  return scored.slice(0, limit);
}

// ---- Load and build the index ----
async function init() {
  try {
    const resp = await fetch("index.json");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    allDocs = await resp.json();
    docMeta = buildMeta(allDocs);
    document.getElementById("result-count").textContent = `${allDocs.length} pages indexed`;
  } catch (e) {
    document.getElementById("result-count").textContent = "Failed to load index";
    console.error(e);
  }
  // Deep links (both resolve against the index, so they work offline):
  //   ?page=cpp/algorithm/max.html   → open that page directly (shareable URL)
  //   ?search=std::max   (or ?q=)    → run the search, show the result list
  // `page` wins if both are present (a page is a more specific intent).
  const params = new URLSearchParams(location.search);
  const page = params.get("page");
  if (page) {
    loadPage(page);
    return;
  }
  const q = params.get("search") || params.get("q");
  if (q) {
    const input = document.getElementById("search-input");
    input.value = q;
    doSearch(q);
  }
}

// ---- Search (UI glue over the deterministic `search()` scorer above) ----
function doSearch(query) {
  currentQuery = query;
  const resultsDiv = document.getElementById("results");
  const countSpan = document.getElementById("result-count");

  if (!query.trim()) {
    resultsDiv.innerHTML = "";
    countSpan.textContent = `${allDocs.length} pages indexed`;
    return;
  }

  const results = search(query, 30);

  countSpan.textContent = `${results.length} result${results.length !== 1 ? "s" : ""}`;

  if (results.length === 0) {
    resultsDiv.innerHTML = `<p style="color:var(--muted);text-align:center;padding:1rem">No results for "${escapeHtml(query)}"</p>`;
    return;
  }

  resultsDiv.innerHTML = results
    .map(
      (r) => `
    <div class="result-item" data-url="${escapeAttr(r.url)}">
      <div class="result-title">${escapeHtml(r.title)}</div>
      ${r.snippet ? `<div class="result-snippet">${escapeHtml(r.snippet)}</div>` : ""}
    </div>`
    )
    .join("");

  // Attach click handlers
  resultsDiv.querySelectorAll(".result-item").forEach((item) => {
    item.addEventListener("click", () => loadPage(item.dataset.url));
  });
}

// ---- Page loading ----
async function loadPage(urlPath) {
  const content = document.getElementById("reader-content");
  const backBtn = document.getElementById("back-btn");
  const titleSpan = document.getElementById("page-title");

  backBtn.style.display = "inline-block";
  content.innerHTML = '<p style="color:var(--muted)">Loading…</p>';

  try {
    let htmlText;
    try {
      const localResp = await fetch(`docs/${urlPath}`);
      if (localResp.ok) {
        htmlText = await localResp.text();
      } else {
        throw new Error("not local");
      }
    } catch {
      const extResp = await fetch(`https://en.cppreference.com/w/${urlPath}`);
      htmlText = await extResp.text();
    }

    // Extract #mw-content-text if present (full cppreference pages)
    const m = htmlText.match(/<div id="mw-content-text"[^>]*>([\s\S]*?)(?:<\/div>\s*<!--|\Z)/);
    let pageContent = m ? m[1] : htmlText;

    // Strip cppreference navigation chrome that clutters the page:
    // - t-navbar: site navigation bar (C++, Compiler support, Language, etc.)
    // - t-nv-begin: header index tables (list of all C++ headers by category)
    // These have relative links that escape /cppmanlite/ and aren't the main content.
    pageContent = stripDivByClass(pageContent, "t-navbar");
    pageContent = stripDivByClass(pageContent, "t-navbar-sep");
    pageContent = stripDivByClass(pageContent, "t-navbar-head");
    // Strip t-nv-begin tables (header category index — 20KB+ of links to other headers)
    pageContent = pageContent.replace(/<table class="t-nv-begin"[\s\S]*?<\/table>/g, "");

    // Clean: strip scripts, styles, comments, edit sections
    pageContent = pageContent.replace(/<script[^>]*>[\s\S]*?<\/script>/g, "");
    pageContent = pageContent.replace(/<style[^>]*>[\s\S]*?<\/style>/g, "");
    pageContent = pageContent.replace(/<!--[\s\S]*?-->/g, "");
    pageContent = pageContent.replace(/<span class="mw-editsection">[\s\S]*?<\/span>/g, "");

    // Fix absolute URLs to point to cppreference.com
    pageContent = pageContent.replace(/href="\/w\//g, 'href="https://en.cppreference.com/w/');
    pageContent = pageContent.replace(/href="\/cpp\//g, 'href="https://en.cppreference.com/cpp/');
    pageContent = pageContent.replace(/src="\//g, 'src="https://en.cppreference.com/');

    // Rewrite relative hrefs so they stay within /cppmanlite/
    const pageDir = urlPath.includes("/") ? urlPath.substring(0, urlPath.lastIndexOf("/") + 1) : "";
    currentPageDir = pageDir;
    pageContent = pageContent.replace(/href="([^"]+)"/g, (match, href) => {
      if (href.startsWith("http") || href.startsWith("/") || href.startsWith("#") || href.startsWith("data:") || href.startsWith("mailto:")) return match;
      const resolved = resolveRelative(pageDir, href.split("#")[0]);
      return `href="docs/${resolved}${href.includes("#") ? "#" + href.split("#")[1] : ""}"`;
    });
    // Rewrite relative src (images, etc.) so they resolve to real local files.
    // The archive pages reference shared assets with relative paths like
    // "../../../../common/images/7/7c/foo.svg" — N ".." that, in the source
    // archive, climb out to reference/ where common/ lives. Our build strips
    // "en/" so the page is docs/<...>/page.html and common/ ships at the site
    // root. Resolve against the page's FULL deployed path (docs/<pageDir>) so
    // the ".." count climbs out of docs/ to the site root exactly as intended.
    // The result already carries "docs/" when it stays inside, and drops it
    // when it escapes — so no extra prefix is added here (unlike href below).
    pageContent = pageContent.replace(/src="([^"]+)"/g, (match, src) => {
      if (src.startsWith("http") || src.startsWith("/") || src.startsWith("#") || src.startsWith("data:")) return match;
      return `src="${resolveRelative("docs/" + pageDir, src)}"`;
    });

    content.innerHTML = pageContent;
    content.scrollTop = 0;

    // Reflect the open page in the URL (?page=<path>) so it's copy-shareable
    // and the browser back button returns to the previous view. Using
    // replaceState (not pushState) keeps history clean while still making the
    // address bar a valid deep link to this page.
    try {
      // encodeURI (not encodeURIComponent) keeps the "/" separators readable:
      //   ?page=cpp/algorithm/max.html  — clean, copy-pasteable, still valid.
      history.replaceState(null, "", "?page=" + encodeURI(urlPath));
    } catch (_) { /* non-file:// contexts */ }

    // Update title in header
    const doc = allDocs.find((d) => d.url === urlPath);
    titleSpan.textContent = doc ? doc.title : urlPath;

    // Highlight active result in sidebar
    document.querySelectorAll(".result-item").forEach((item) => {
      item.classList.toggle("active", item.dataset.url === urlPath);
    });
  } catch (e) {
    content.innerHTML = `<p>Failed to load page: ${escapeHtml(e.message)}</p>
      <p><a href="https://en.cppreference.com/w/${urlPath}" target="_blank">Open on cppreference.com →</a></p>`;
  }
}

// ---- Helpers ----
function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s || "";
  return d.innerHTML;
}
function escapeAttr(s) {
  return escapeHtml(s).replace(/"/g, "&quot;");
}

// Strip a <div class="className">...</div> block, handling nested divs.
function stripDivByClass(html, className) {
  const re = new RegExp(`<div class="${className}"`, "g");
  let result = html;
  let match;
  while ((match = re.exec(result)) !== null) {
    const start = match.index;
    let depth = 0;
    let i = start;
    while (i < result.length) {
      if (result.substring(i, i + 4) === "<div") depth++;
      else if (result.substring(i, i + 6) === "</div>") {
        depth--;
        if (depth === 0) {
          result = result.substring(0, start) + result.substring(i + 6);
          re.lastIndex = start; // restart search from this position
          break;
        }
      }
      i++;
    }
  }
  return result;
}

// Resolve a relative path against a base directory.
function resolveRelative(baseDir, relPath) {
  const baseParts = baseDir.split("/").filter(Boolean);
  const relParts = relPath.split("/");
  for (const part of relParts) {
    if (part === "..") baseParts.pop();
    else if (part !== "." && part !== "") baseParts.push(part);
  }
  return baseParts.join("/");
}

// ---- Event wiring ----
document.getElementById("search-input").addEventListener("input", (e) => {
  doSearch(e.target.value);
});

// Keyboard: Enter opens first result (manpage behavior)
document.getElementById("search-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const first = document.querySelector(".result-item");
    if (first) first.click();
  }
});

// Back button: return focus to search results
document.getElementById("back-btn").addEventListener("click", () => {
  document.getElementById("search-input").focus();
  document.getElementById("search-input").select();
});

// Event delegation: intercept link clicks inside reader-content
document.getElementById("reader-content").addEventListener("click", (e) => {
  const a = e.target.closest("a");
  if (!a) return;
  const href = a.getAttribute("href");
  if (!href) return;
  e.preventDefault();
  if (href.startsWith("http")) {
    window.open(href, "_blank");
  } else if (href.startsWith("#")) {
    const el = document.getElementById("reader-content").querySelector(href);
    if (el) el.scrollIntoView({ behavior: "smooth" });
  } else if (href.startsWith("docs/")) {
    // Rewritten relative link — extract page path
    const hashIdx = href.indexOf("#");
    const path = hashIdx >= 0 ? href.substring(5, hashIdx) : href.substring(5);
    loadPage(path);
    if (hashIdx >= 0) {
      // Scroll to anchor after load
      setTimeout(() => {
        const anchor = document.getElementById("reader-content").querySelector(href.substring(hashIdx));
        if (anchor) anchor.scrollIntoView({ behavior: "smooth" });
      }, 500);
    }
  } else {
    loadPage(resolveRelative(currentPageDir, href));
  }
});

// ---- Theme: auto (follow system) / light / dark ----
// The CSS base is dark; `@media (prefers-color-scheme: light)` gives light for
// system-light users. An explicit `data-theme="light|dark"` on <html> overrides
// that (higher specificity) for when the user has chosen one. "auto" removes
// the attribute so the system preference wins again. The choice is persisted in
// localStorage and applied before first paint by an inline script in <head>
// (no flash of the wrong theme).
const THEME_KEY = "cppmanlite-theme";
const THEME_ORDER = ["auto", "light", "dark"];
let themeBtn = null;
function storedTheme() {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "auto";
  } catch (_) { return "auto"; }
}
function applyTheme(mode) {
  const root = document.documentElement;
  // data-theme drives the page palette (auto => remove, so system CSS wins).
  if (mode === "auto") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", mode);
  // data-theme-state drives the button's icon (CSS shows one of the three
  // inline glyph groups). Kept in sync with the palette on every change.
  themeBtn.setAttribute("data-theme-state", mode);
  const next = THEME_ORDER[(THEME_ORDER.indexOf(mode) + 1) % THEME_ORDER.length];
  themeBtn.title = `Theme: ${mode} (click for ${next})`;
  themeBtn.setAttribute("aria-label", `Theme: ${mode}. Activate to switch to ${next}.`);
}
function cycleTheme() {
  const cur = storedTheme();
  const next = THEME_ORDER[(THEME_ORDER.indexOf(cur) + 1) % THEME_ORDER.length];
  try {
    if (next === "auto") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, next);
  } catch (_) {}
  applyTheme(next);
}
function initTheme() {
  themeBtn = document.getElementById("theme-toggle");
  if (!themeBtn) return;
  themeBtn.addEventListener("click", cycleTheme);
  // The inline <head> script already set data-theme + data-theme-state before
  // first paint; re-assert here (harmless) so title/aria-label are correct.
  applyTheme(storedTheme());
}

// Register the service worker (offline + installable PWA). Best-effort and
// deploy-agnostic: sw.js is referenced relative to this file's own URL, so the
// same build works from any host/path. No-op in unsupported/insecure contexts.
if ("serviceWorker" in navigator && isSecureContext) {
  const swUrl = new URL("sw.js", document.baseURI).href;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register(swUrl).catch((e) => {
      console.warn("SW registration failed:", e.message);
    });
  });
}

// Init
initTheme();
init();
