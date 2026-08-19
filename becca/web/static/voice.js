/* Voice preview (FR-AGENT-11): fetch the proxied sample through our
 * backend and play it. One player; clicking another voice stops the
 * current one. Preview honours the speed field, clamped server-side. */
(function () {
  "use strict";

  var current = null;

  document.querySelectorAll(".preview-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (current) {
        current.pause();
        current = null;
      }
      var speed = (document.getElementById("voice_speed") || {}).value || "1.0";
      var agentPath = window.location.pathname.replace(/\/voice$/, "");
      var url =
        agentPath +
        "/voice/preview?voice=" +
        encodeURIComponent(btn.dataset.voice) +
        "&speed=" +
        encodeURIComponent(speed);
      btn.textContent = "…";
      fetch(url)
        .then(function (r) {
          if (!r.ok) throw new Error("preview failed");
          return r.blob();
        })
        .then(function (blob) {
          btn.textContent = "▶";
          current = new Audio(URL.createObjectURL(blob));
          current.play();
        })
        .catch(function () {
          btn.textContent = "✕";
          setTimeout(function () {
            btn.textContent = "▶";
          }, 1500);
        });
    });
  });
})();
