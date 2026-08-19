/* Dictation for brief boxes — Web Speech API, no backend.
 *
 * Usage: <div class="dictate" data-dictate="#brief"></div> + this script.
 * The div stays empty (and invisible) on browsers without SpeechRecognition,
 * so typing is always the baseline and nobody sees a broken control.
 *
 * Transcripts APPEND to the textarea; the user can type or edit at any time,
 * including while listening. Chrome ends recognition sessions after short
 * silences, so we restart on `end` until the user presses Stop.
 */
(function () {
  "use strict";

  var Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) return;

  var MIC_SVG =
    '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<rect x="9" y="2" width="6" height="12" rx="3"></rect>' +
    '<path d="M5 10v1a7 7 0 0 0 14 0v-1"></path>' +
    '<line x1="12" y1="18" x2="12" y2="22"></line></svg>';

  var STOP_SVG =
    '<svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
    '<rect x="5" y="5" width="14" height="14" rx="2"></rect></svg>';

  function needsSpace(s) {
    return s.length > 0 && !/\s$/.test(s);
  }

  function init(host) {
    var target = document.querySelector(host.getAttribute("data-dictate"));
    if (!target) return;

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-ghost btn-sm dictate-btn";

    // Becca's voice pulse (same element as the agents-list empty state),
    // revealed only while speech is actually being heard.
    var wave = document.createElement("span");
    wave.className = "pulse";
    wave.setAttribute("aria-hidden", "true");
    wave.hidden = true;
    for (var i = 0; i < 5; i++) wave.appendChild(document.createElement("span"));

    var status = document.createElement("span");
    status.className = "meta-sm dictate-status";
    status.hidden = true;
    status.setAttribute("role", "status");

    host.appendChild(btn);
    host.appendChild(wave);
    host.appendChild(status);

    var rec = null;
    var listening = false; // user intent: keep going until Stop
    var base = "";         // committed text; interim results render after it
    var selfWrite = false;

    function render(idle) {
      if (idle) {
        btn.innerHTML = MIC_SVG + " Dictate";
        btn.setAttribute("aria-pressed", "false");
        wave.hidden = true;
        status.hidden = true;
      } else {
        btn.innerHTML = STOP_SVG + " Stop";
        btn.setAttribute("aria-pressed", "true");
        status.hidden = false;
        status.textContent = "LISTENING — JUST TALK";
      }
    }
    render(true);

    function setValue(v) {
      selfWrite = true;
      target.value = v;
      target.dispatchEvent(new Event("input", { bubbles: true }));
      selfWrite = false;
    }

    function makeRec() {
      var r = new Recognition();
      r.continuous = true;
      r.interimResults = true;

      r.onresult = function (ev) {
        var interim = "";
        for (var i = ev.resultIndex; i < ev.results.length; i++) {
          var chunk = ev.results[i][0].transcript;
          if (ev.results[i].isFinal) {
            base += (needsSpace(base) ? " " : "") + chunk.trim();
          } else {
            interim += chunk;
          }
        }
        interim = interim.trim();
        setValue(base + (interim && needsSpace(base) ? " " : "") + interim);
      };

      r.onerror = function (ev) {
        if (ev.error === "not-allowed" || ev.error === "service-not-allowed") {
          stop();
          btn.disabled = true;
          btn.innerHTML = MIC_SVG + " Mic blocked";
          btn.title = "Allow microphone access in your browser to dictate.";
        }
        // "no-speech" / "network" etc: onend fires next and the restart
        // loop below decides what happens.
      };

      r.onspeechstart = function () { if (listening) wave.hidden = false; };
      r.onspeechend = function () { wave.hidden = true; };

      r.onend = function () {
        wave.hidden = true;
        if (!listening) return;
        // Session ended on us (silence timeout) — resume seamlessly.
        try {
          r.start();
        } catch (_) {
          stop();
        }
      };

      return r;
    }

    function start() {
      base = target.value.replace(/\s+$/, "");
      rec = makeRec();
      try {
        rec.start();
      } catch (_) {
        return;
      }
      listening = true;
      render(false);
      target.focus();
    }

    function stop() {
      listening = false;
      if (rec) {
        rec.onend = null;
        try { rec.stop(); } catch (_) { /* already stopped */ }
        rec = null;
      }
      render(true);
    }

    btn.addEventListener("click", function () {
      if (listening) stop();
      else start();
    });

    // If the user types or pastes mid-dictation, adopt their edit as the new
    // committed text instead of clobbering it on the next result.
    target.addEventListener("input", function () {
      if (!selfWrite) base = target.value.replace(/\s+$/, "");
    });

    // Don't leave the mic running past submit.
    var form = target.form;
    if (form) form.addEventListener("submit", stop);
  }

  document.querySelectorAll("[data-dictate]").forEach(init);
})();
