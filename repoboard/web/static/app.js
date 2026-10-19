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
