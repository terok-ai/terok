import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";

mermaid.initialize({
  startOnLoad: false,
  theme: "dark",
});

window.mermaid = mermaid;

// Material for MkDocs re-initializes mermaid and injects ID-scoped
// styles into each SVG that use var(--md-default-fg-color--lightest)
// for cluster backgrounds.  Override this variable on each rendered
// SVG so clusters become transparent.
new MutationObserver(function () {
  document.querySelectorAll("svg[id^='__mermaid']").forEach(function (svg) {
    svg.style.setProperty("--md-default-fg-color--lightest", "transparent");
    svg.style.setProperty("--md-default-fg-color--lighter", "transparent");
  });
}).observe(document.body, { childList: true, subtree: true });
