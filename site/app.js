// cppmanlite — client-side search + manpage-style reader
// Two-panel layout: sidebar (search + results) | main (page content)
// Press Enter to open the top result directly, like `man`.

let lunrIndex = null;
let allDocs = [];
let currentQuery = "";
let currentPageDir = ""; // dir of the currently displayed page, for resolving relative links

// ---- Load and build the index ----
async function init() {
  try {
    const resp = await fetch("index.json");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    allDocs = await resp.json();
    lunrIndex = lunr(function () {
      this.ref("id");
      this.field("title", { boost: 10 });
      this.field("url", { boost: 5 });
      this.field("snippet");
      allDocs.forEach((doc, i) => {
        this.add({ id: i, title: doc.title, url: doc.url, snippet: doc.snippet });
      });
    });
    document.getElementById("result-count").textContent = `${allDocs.length} pages indexed`;
  } catch (e) {
    document.getElementById("result-count").textContent = "Failed to load index";
    console.error(e);
  }
}

// ---- Search ----
function doSearch(query) {
  currentQuery = query;
  const resultsDiv = document.getElementById("results");
  const countSpan = document.getElementById("result-count");

  if (!query.trim()) {
    resultsDiv.innerHTML = "";
    countSpan.textContent = `${allDocs.length} pages indexed`;
    return;
  }

  // lunr search + fallback to simple substring match
  let results = [];
  try {
    const lunrResults = lunrIndex.search(query);
    results = lunrResults.map((r) => {
      const doc = allDocs[parseInt(r.ref)];
      return { ...doc, score: r.score };
    });
  } catch (e) {
    // lunr throws on syntax errors — fall back to substring
  }

  // Fallback: if lunr returns nothing, do substring search
  if (results.length === 0) {
    const q = query.toLowerCase();
    const qNorm = q.replace(/^std::/, "");
    results = allDocs
      .filter(
        (d) =>
          d.title.toLowerCase().includes(q) ||
          d.url.toLowerCase().includes(q) ||
          (qNorm && d.title.toLowerCase().includes(qNorm))
      )
      .slice(0, 30)
      .map((d) => ({ ...d, score: 0 }));
  }

  results = results.slice(0, 30);

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

    // Extract #mw-content-text
    const m = htmlText.match(/<div id="mw-content-text"[^>]*>([\s\S]*?)(?:<\/div>\s*<!--|\Z)/);
    let pageContent = m ? m[1] : htmlText;

    // Clean: strip scripts, styles, comments, edit sections
    pageContent = pageContent.replace(/<script[^>]*>[\s\S]*?<\/script>/g, "");
    pageContent = pageContent.replace(/<style[^>]*>[\s\S]*?<\/style>/g, "");
    pageContent = pageContent.replace(/<!--[\s\S]*?-->/g, "");
    pageContent = pageContent.replace(/<span class="mw-editsection">[\s\S]*?<\/span>/g, "");

    // Fix absolute URLs to point to cppreference.com
    pageContent = pageContent.replace(/href="\/w\//g, 'href="https://en.cppreference.com/w/');
    pageContent = pageContent.replace(/href="\/cpp\//g, 'href="https://en.cppreference.com/cpp/');
    pageContent = pageContent.replace(/src="\//g, 'src="https://en.cppreference.com/');

    // Rewrite relative src (images, etc.) so they load from docs/ bundle
    const pageDir = urlPath.includes("/") ? urlPath.substring(0, urlPath.lastIndexOf("/") + 1) : "";
    currentPageDir = pageDir;
    pageContent = pageContent.replace(/src="([^"]+)"/g, (match, src) => {
      if (src.startsWith("http") || src.startsWith("/") || src.startsWith("#") || src.startsWith("data:")) return match;
      return `src="docs/${resolveRelative(pageDir, src)}"`;
    });

    content.innerHTML = pageContent;
    content.scrollTop = 0;

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
  } else {
    loadPage(resolveRelative(currentPageDir, href));
  }
});

// Init
init();
