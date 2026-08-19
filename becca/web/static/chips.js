/* The chip editor (FR-AGENT-5A/5B, FR-AGENT-3B/3B1).
 *
 * The DOM is an editing surface, never a storage format. On submit the
 * surface is walked into script blocks: text nodes become text blocks,
 * chips become field_refs carrying the field id. There is no way to type
 * a reference — insertion goes through the menu — and pasted {{mustache}}
 * is converted to a chip immediately, creating the field if needed.
 *
 * Browsers already treat contenteditable="false" islands as indivisible:
 * the caret sits before or after a chip, never inside, and backspace
 * removes it whole. This file adds serialisation, the insert menu, paste
 * hygiene and the output-field rows — not chip atomicity.
 */
(function () {
  "use strict";

  var editor = document.getElementById("script-editor");
  var form = document.getElementById("review-form");
  if (!editor || !form) return;

  var fieldData = JSON.parse(document.getElementById("field-data").textContent);
  // fieldsById holds every field definition we know, both kinds.
  var fieldsById = {};
  var maxId = 0;
  fieldData.forEach(function (f) {
    fieldsById[f.id] = f;
    if (f.id > maxId) maxId = f.id;
  });

  function newInputField(key) {
    maxId += 1;
    var f = { id: maxId, key: key, kind: "input", required: true, type: "text",
              values: [], instructions: "" };
    fieldsById[f.id] = f;
    return f;
  }

  function chipEl(field) {
    var span = document.createElement("span");
    span.className = "v-chip";
    span.setAttribute("contenteditable", "false");
    span.dataset.fieldId = String(field.id);
    span.textContent = field.key;
    return span;
  }

  /* ---- serialisation: DOM walk, not parsing ---- */

  function serialiseScript() {
    // Recursive walk: chips anywhere become field_refs, <br> and block
    // boundaries become newlines, every other element flattens to its
    // children (so display-only <b> labels survive as plain text).
    var blocks = [];
    function pushText(t) {
      if (!t) return;
      var last = blocks[blocks.length - 1];
      if (last && last.type === "text") last.content += t;
      else blocks.push({ type: "text", content: t });
    }
    function walk(node) {
      node.childNodes.forEach(function (child) {
        if (child.nodeType === Node.TEXT_NODE) {
          pushText(child.textContent);
        } else if (child.classList && child.classList.contains("v-chip")) {
          blocks.push({ type: "field_ref", field_id: parseInt(child.dataset.fieldId, 10) });
        } else if (child.nodeName === "BR") {
          pushText("\n");
        } else if (child.nodeType === Node.ELEMENT_NODE) {
          var isBlock = /^(DIV|P)$/.test(child.nodeName);
          if (isBlock && blocks.length) pushText("\n");
          walk(child);
        }
      });
    }
    walk(editor);
    return blocks;
  }

  function referencedIds(blocks) {
    var ids = {};
    blocks.forEach(function (b) { if (b.type === "field_ref") ids[b.field_id] = true; });
    return ids;
  }

  function serialiseOutputs() {
    var rows = document.querySelectorAll("#outputs-editor .output-row");
    var outputs = [];
    rows.forEach(function (row) {
      var key = row.querySelector(".output-key").value.trim();
      if (!key) return;
      var type = row.querySelector(".output-type").value;
      var values = row.querySelector(".output-values").value
        .split(",").map(function (v) { return v.trim(); }).filter(Boolean);
      var id = parseInt(row.dataset.fieldId, 10);
      if (!id) { maxId += 1; id = maxId; }
      outputs.push({
        id: id, key: key, kind: "output",
        required: row.querySelector(".output-required").checked,
        type: type, values: type === "enum" ? values : [],
        instructions: (fieldsById[id] && fieldsById[id].instructions) || ""
      });
    });
    return outputs;
  }

  form.addEventListener("submit", function () {
    var blocks = serialiseScript();
    var refs = referencedIds(blocks);
    // An input field exists precisely because the script uses it
    // (FR-AGENT-3B): unreferenced inputs drop out here.
    var fields = [];
    Object.keys(fieldsById).forEach(function (id) {
      var f = fieldsById[id];
      if (f.kind === "input" && refs[f.id]) fields.push(f);
    });
    serialiseOutputs().forEach(function (f) { fields.push(f); });
    document.getElementById("content-json").value =
      JSON.stringify({ fields: fields, script_blocks: blocks });
  });

  /* ---- insert menu (FR-AGENT-3B): existing input fields + New field ---- */

  var menu = null;
  function closeMenu() { if (menu) { menu.remove(); menu = null; } }

  function openMenu(anchorRect) {
    closeMenu();
    menu = document.createElement("div");
    menu.className = "panel";
    menu.style.cssText = "position:fixed; z-index:50; padding:0.25rem; min-width:12rem;" +
      "left:" + anchorRect.left + "px; top:" + (anchorRect.bottom + 4) + "px";
    var inputs = Object.keys(fieldsById)
      .map(function (id) { return fieldsById[id]; })
      .filter(function (f) { return f.kind === "input"; });
    inputs.forEach(function (f) {
      menu.appendChild(menuItem(f.key, function () { insertChip(f); }));
    });
    menu.appendChild(menuItem("+ New field…", function () {
      var key = window.prompt("Field name (snake_case):");
      if (key && /^[a-z][a-z0-9_]*$/.test(key)) insertChip(newInputField(key));
    }));
    document.body.appendChild(menu);
  }

  function menuItem(label, onPick) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "btn btn-ghost btn-sm";
    b.style.cssText = "display:block; width:100%; text-align:left";
    b.textContent = label;
    b.addEventListener("mousedown", function (e) { e.preventDefault(); onPick(); closeMenu(); });
    return b;
  }

  var savedRange = null;
  function saveCaret() {
    var sel = window.getSelection();
    if (sel.rangeCount && editor.contains(sel.anchorNode)) savedRange = sel.getRangeAt(0).cloneRange();
  }

  function insertChip(field) {
    editor.focus();
    var sel = window.getSelection();
    if (savedRange) { sel.removeAllRanges(); sel.addRange(savedRange); }
    var range = sel.rangeCount ? sel.getRangeAt(0) : null;
    if (!range || !editor.contains(range.startContainer)) {
      range = document.createRange();
      range.selectNodeContents(editor);
      range.collapse(false);
    }
    // If the trigger was "/", it sits just before the caret — remove it.
    var node = range.startContainer;
    if (node.nodeType === Node.TEXT_NODE && range.startOffset > 0 &&
        node.textContent[range.startOffset - 1] === "/") {
      node.deleteData(range.startOffset - 1, 1);
      range.setStart(node, range.startOffset - 1);
    }
    range.collapse(true);
    var chip = chipEl(field);
    range.insertNode(chip);
    range.setStartAfter(chip);
    range.collapse(true);
    sel.removeAllRanges();
    sel.addRange(range);
    refreshInputsView();
  }

  editor.addEventListener("keydown", function (e) {
    if (e.key === "/") {
      setTimeout(function () {
        saveCaret();
        var sel = window.getSelection();
        if (!sel.rangeCount) return;
        openMenu(sel.getRangeAt(0).getBoundingClientRect());
      }, 0);
    } else if (e.key === "Escape") {
      closeMenu();
    }
  });

  document.getElementById("insert-field").addEventListener("mousedown", function (e) {
    e.preventDefault();
    saveCaret();
    openMenu(e.target.getBoundingClientRect());
  });

  document.addEventListener("click", function (e) {
    if (menu && !menu.contains(e.target)) closeMenu();
  });

  /* ---- paste hygiene (FR-AGENT-3B1): {{x}} never stays literal ---- */

  editor.addEventListener("paste", function (e) {
    e.preventDefault();
    var pasted = (e.clipboardData || window.clipboardData).getData("text/plain");
    var sel = window.getSelection();
    if (!sel.rangeCount) return;
    var range = sel.getRangeAt(0);
    range.deleteContents();
    var parts = pasted.split(/(\{\{\s*[a-z][a-z0-9_]*\s*\}\})/g);
    parts.forEach(function (part) {
      var m = part.match(/^\{\{\s*([a-z][a-z0-9_]*)\s*\}\}$/);
      var node;
      if (m) {
        var existing = null;
        Object.keys(fieldsById).forEach(function (id) {
          if (fieldsById[id].key === m[1] && fieldsById[id].kind === "input") existing = fieldsById[id];
        });
        node = chipEl(existing || newInputField(m[1]));
      } else {
        node = document.createTextNode(part);
      }
      range.insertNode(node);
      range.setStartAfter(node);
      range.collapse(true);
    });
    sel.removeAllRanges();
    sel.addRange(range);
    refreshInputsView();
  });

  editor.addEventListener("input", refreshInputsView);

  /* ---- the read-only inputs view follows the script (FR-AGENT-3B) ---- */

  function refreshInputsView() {
    var view = document.getElementById("inputs-view");
    if (!view) return;
    var refs = referencedIds(serialiseScript());
    view.innerHTML = "";
    Object.keys(fieldsById).forEach(function (id) {
      var f = fieldsById[id];
      if (f.kind !== "input" || !refs[f.id]) return;
      var row = document.createElement("div");
      row.className = "schema-row";
      row.innerHTML = '<span class="tag var"></span><span class="meta-sm"></span>';
      row.querySelector(".tag").textContent = f.key;
      row.querySelector(".meta-sm").textContent = f.required ? "REQUIRED" : "OPTIONAL";
      view.appendChild(row);
    });
  }

  /* ---- output rows: plain editable list; nothing references them ---- */

  document.getElementById("add-output").addEventListener("click", function () {
    var row = document.createElement("div");
    row.className = "schema-row output-row";
    row.dataset.fieldId = "";
    row.innerHTML =
      '<input class="output-key" placeholder="field_name" aria-label="Field name">' +
      '<select class="output-type" aria-label="Type">' +
      '<option value="enum">ENUM</option><option value="text">TEXT</option></select>' +
      '<input class="output-values" placeholder="values, comma-separated" aria-label="Enum values">' +
      '<label class="meta-sm"><input type="checkbox" class="output-required" checked> REQUIRED</label>' +
      '<button class="btn btn-ghost btn-sm output-remove" type="button" aria-label="Remove">✕</button>';
    document.getElementById("outputs-editor").appendChild(row);
  });

  document.getElementById("outputs-editor").addEventListener("click", function (e) {
    if (e.target.classList.contains("output-remove")) {
      e.target.closest(".output-row").remove();
    }
  });

  document.getElementById("outputs-editor").addEventListener("change", function (e) {
    if (e.target.classList.contains("output-type")) {
      var values = e.target.closest(".output-row").querySelector(".output-values");
      values.hidden = e.target.value !== "enum";
    }
  });

  /* ---- unsaved-edits guard (user feedback, 14 Aug 2026): once anything
     is edited, "Continue to voice" locks until the edits are saved — an
     unsaved change would silently vanish on navigation. ---- */

  var continueLink = document.getElementById("continue-voice");
  var dirtyHint = document.getElementById("dirty-hint");
  var dirty = false;

  function markDirty() {
    if (dirty || !continueLink) return;
    dirty = true;
    continueLink.setAttribute("aria-disabled", "true");
    continueLink.style.opacity = "0.45";
    continueLink.title = "Save your edits first — they would be lost";
    if (dirtyHint) dirtyHint.hidden = false;
  }

  if (continueLink) {
    continueLink.addEventListener("click", function (e) {
      if (dirty) e.preventDefault();
    });
    editor.addEventListener("input", markDirty);
    var outputs = document.getElementById("outputs-editor");
    outputs.addEventListener("input", markDirty);
    outputs.addEventListener("change", markDirty);
    outputs.addEventListener("click", function (e) {
      if (e.target.classList.contains("output-remove")) markDirty();
    });
    document.getElementById("add-output").addEventListener("click", markDirty);
  }
})();
