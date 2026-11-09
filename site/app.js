// Hash router (#home, #projects, #meetings, #alerts, #about, #p-<id>) plus table filters.
// Without JS every section simply shows in sequence.
(function () {
  document.documentElement.classList.add("js");
  var views = Array.prototype.slice.call(document.querySelectorAll("[data-view]"));
  var navLinks = document.querySelectorAll("nav a");

  function show() {
    var id = (location.hash || "#home").slice(1);
    var target = document.getElementById(id);
    if (!target || !target.hasAttribute("data-view")) target = document.getElementById("home");
    views.forEach(function (v) { v.classList.toggle("on", v === target); });
    var section = target.id.indexOf("p-") === 0 ? "projects" : target.id;
    navLinks.forEach(function (a) {
      if (a.getAttribute("href") === "#" + section) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    window.scrollTo(0, 0);
  }
  window.addEventListener("hashchange", show);
  show();

  // project filters
  var table = document.getElementById("alltable");
  var q = document.getElementById("q");
  var mode = "active";
  function filter() {
    var term = (q.value || "").toLowerCase();
    table.querySelectorAll("tbody tr").forEach(function (tr) {
      var okMode = mode === "all" || tr.getAttribute("data-active") === "1";
      var okText = !term || tr.textContent.toLowerCase().indexOf(term) !== -1;
      tr.hidden = !(okMode && okText);
    });
  }
  document.querySelectorAll("[data-filter]").forEach(function (b) {
    b.addEventListener("click", function () {
      mode = b.getAttribute("data-filter");
      document.querySelectorAll("[data-filter]").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
      filter();
    });
  });
  if (q) q.addEventListener("input", filter);
  if (table) filter();

  // meetings: only land-use
  var onlylu = document.getElementById("onlylu");
  var agendas = document.querySelector(".agendas");
  function lu() { if (agendas) agendas.classList.toggle("only-lu", onlylu.checked); }
  if (onlylu) { onlylu.addEventListener("change", lu); lu(); }
})();

// copy a drafted records request; fall back to selecting the text when the clipboard is refused
document.querySelectorAll("[data-copy]").forEach(function (btn) {
  btn.addEventListener("click", function () {
    var ta = document.getElementById(btn.getAttribute("data-copy"));
    function selected() { ta.focus(); ta.select(); btn.textContent = "Selected: press Ctrl+C"; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(ta.value).then(function () { btn.textContent = "Copied"; }, selected);
    } else { selected(); }
    setTimeout(function () { btn.textContent = "Copy text"; }, 2500);
  });
});
