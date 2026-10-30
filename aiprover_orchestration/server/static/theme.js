// Color theme: a link's ?theme= (trace pages run in a sandbox without storage),
// then the viewer's last choice, then white. The picker has the id "theme", links
// marked data-keep-theme carry the theme to the next page, and images marked
// data-themed-image come in one version per theme. A trace page reached from the
// server's page learns that page from ?home=, for its link with the id "home".
const THEMES = ["white", "warm", "dark"];
const QUERY = new URLSearchParams(location.search);
const HOME = [QUERY.get("home"), location.pathname].find(path => /^\/(user|dev)$/.test(path));

function savedTheme() {
  try { return localStorage.getItem("aiprover-theme"); } catch { return null; }
}

function setTheme(name) {
  document.documentElement.dataset.theme = name;
  try { localStorage.setItem("aiprover-theme", name); } catch {}
  if (QUERY.has("theme")) {
    const address = new URL(location.href);
    address.searchParams.set("theme", name);
    try { history.replaceState(null, "", address); } catch {}
  }
  for (const link of document.querySelectorAll("a[data-keep-theme]")) {
    link.href = themed(link.getAttribute("href"));
  }
  for (const image of document.querySelectorAll("img[data-themed-image]")) {
    image.src = `/static/guide/${name}/${image.dataset.themedImage}`;
  }
}

function themed(href) {
  const path = href.split("?")[0];
  const home = HOME && path !== HOME ? `&home=${HOME}` : "";
  return `${path}?theme=${document.documentElement.dataset.theme}${home}`;
}

setTheme([QUERY.get("theme"), savedTheme()]
  .find(name => THEMES.includes(name)) || "white");

document.addEventListener("DOMContentLoaded", () => {
  const home = document.getElementById("home");
  if (home && HOME) home.setAttribute("href", HOME);
  else home?.remove();
  const picker = document.getElementById("theme");
  if (!picker) return;
  picker.innerHTML = THEMES.map(name =>
    `<option value="${name}">${name[0].toUpperCase() + name.slice(1)}</option>`).join("");
  picker.value = document.documentElement.dataset.theme;
  picker.onchange = () => setTheme(picker.value);
  setTheme(picker.value);
});
