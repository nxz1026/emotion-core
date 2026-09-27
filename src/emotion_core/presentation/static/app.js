// Theme toggle
function toggleTheme() {
  const html = document.documentElement;
  const current = html.getAttribute("data-theme");
  const next = current === "dark" ? "light" : "dark";
  html.setAttribute("data-theme", next);
  localStorage.setItem("theme", next);
  document.getElementById("theme-toggle").textContent = next === "dark" ? "☀️" : "🌙";
}

// Restore saved theme
(function () {
  const saved = localStorage.getItem("theme");
  if (saved) {
    document.documentElement.setAttribute("data-theme", saved);
    const btn = document.getElementById("theme-toggle");
    if (btn) btn.textContent = saved === "dark" ? "☀️" : "🌙";
  }
})();
