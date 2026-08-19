/* Test-screen schema editor (FR-TEST-1): the output fields are editable
 * here, and each edit applies to the next test call. The script and
 * input fields are carried through unchanged from the embedded base
 * content; only the outputs are rebuilt from the rows. */
(function () {
  "use strict";

  var form = document.getElementById("content-json");
  if (!form) return;
  var base = JSON.parse(document.getElementById("base-content").textContent);
  var maxId = base.fields.reduce(function (m, f) { return Math.max(m, f.id); }, 0);
  var instructionsById = {};
  base.fields.forEach(function (f) { instructionsById[f.id] = f.instructions || ""; });

  document.querySelector('form[action$="/versions"]').addEventListener("submit", function () {
    var outputs = [];
    document.querySelectorAll("#outputs-editor .output-row").forEach(function (row) {
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
        instructions: instructionsById[id] || ""
      });
    });
    var fields = base.fields.filter(function (f) { return f.kind === "input"; }).concat(outputs);
    document.getElementById("content-json").value =
      JSON.stringify({ fields: fields, script_blocks: base.script_blocks });
  });

  document.getElementById("add-output").addEventListener("click", function () {
    var row = document.createElement("div");
    row.className = "schema-row output-row";
    row.dataset.fieldId = "";
    row.innerHTML =
      '<input class="output-key" placeholder="field_name" aria-label="Field name">' +
      '<select class="output-type" aria-label="Type">' +
      '<option value="enum">ENUM</option><option value="text">TEXT</option></select>' +
      '<input class="output-values" placeholder="values, comma-separated" aria-label="Enum values">' +
      '<label class="meta-sm"><input type="checkbox" class="output-required" checked> REQ</label>' +
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
})();
