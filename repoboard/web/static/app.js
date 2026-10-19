// Text size: the A-/A/A+ buttons and Cmd or Ctrl with +, - and 0. The server keeps the value.
(function () {
  // One request at a time, so quick clicks apply in the order the user made them.
  var queue = Promise.resolve();

  function setZoom(step) {
    queue = queue
      .then(function () {
        return fetch("/zoom", {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: "step=" + encodeURIComponent(step),
        });
      })
      .then(function (response) { return response.json(); })
      .then(function (data) { document.documentElement.style.setProperty("--zoom", data.zoom); })
      .catch(function () {});
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-zoom]");
    if (button) setZoom(button.dataset.zoom);
  });

  var keys = { "=": "in", "+": "in", "-": "out", "0": "reset" };
  document.addEventListener("keydown", function (event) {
    var step = keys[event.key];
    if (step && (event.metaKey || event.ctrlKey) && !event.altKey) {
      event.preventDefault();
      setZoom(step);
    }
  });
})();

// Opening an item on GitHub marks it seen and polls its repository again soon. Coming back to
// Repoboard polls the repositories opened before, so merges and closes show up at once.
(function () {
  function post(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body || "",
      keepalive: true,
    });
  }

  function opened(event) {
    var link = event.target.closest("a[data-key]");
    if (!link || event.button > 1) return;
    post("/items/open", "key=" + encodeURIComponent(link.dataset.key))
      .then(function () { htmx.trigger(document.body, "repoboard-updated"); })
      .catch(function () {}); // the next poll catches up when the server did not answer
  }
  document.addEventListener("click", opened);
  document.addEventListener("auxclick", opened); // a middle click opens a tab without a click event

  var lastFocus = 0;
  function back() {
    if (document.visibilityState !== "visible" || Date.now() - lastFocus < 5000) return;
    lastFocus = Date.now();
    post("/focus").catch(function () {});
  }
  window.addEventListener("focus", back);
  document.addEventListener("visibilitychange", back);
})();

// Sections the user collapsed stay collapsed when the content reloads in place.
(function () {
  var KEY = "repoboard.collapsed";
  var collapsed = {};
  try { collapsed = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { collapsed = {}; }

  function apply(root) {
    root.querySelectorAll("details[data-section]").forEach(function (section) {
      if (collapsed[section.dataset.section]) section.open = false;
    });
  }

  document.addEventListener("toggle", function (event) {
    var section = event.target;
    if (!section.matches?.("details[data-section]")) return;
    if (section.open) delete collapsed[section.dataset.section];
    else collapsed[section.dataset.section] = true;
    try { localStorage.setItem(KEY, JSON.stringify(collapsed)); } catch (e) { /* a private window has no storage */ }
  }, true);

  document.addEventListener("DOMContentLoaded", function () { apply(document); });
  document.body.addEventListener("htmx:afterSwap", function (event) { apply(event.detail.target.ownerDocument); });
})();

// Keyboard focus survives a live update. The focused control comes back by its item and action. When
// its row is gone, as after "Seen", the same control of the row that took its place gets the focus.
(function () {
  var saved = null;

  function controls(action) {
    return Array.prototype.slice.call(document.querySelectorAll('#content [data-action="' + action + '"]'));
  }

  document.body.addEventListener("htmx:beforeSwap", function (event) {
    var active = document.activeElement;
    saved = null;
    if (event.detail.target.id !== "content" || !active || !active.dataset || !active.dataset.action) return;
    saved = { key: active.dataset.key, action: active.dataset.action, index: controls(active.dataset.action).indexOf(active) };
  });

  document.body.addEventListener("htmx:afterSettle", function () {
    if (!saved) return;
    var same = controls(saved.action);
    var match = same.filter(function (element) { return element.dataset.key === saved.key; })[0];
    var target = match || same[Math.min(saved.index, same.length - 1)];
    saved = null;
    if (target) target.focus();
  });
})();
