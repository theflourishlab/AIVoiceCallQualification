"""docs/evals/index.html — the review surface for scorecards.

One self-contained file, regenerated from every batch JSON in the
directory on each eval run: open it from disk, no server, no external
requests. All rendering happens client-side from the embedded data, so
this module only concatenates; the JSON written by report.py stays the
single source of truth.
"""

import json
from pathlib import Path
from typing import Any

# </script> inside the embedded JSON would end the data block early.
_SCRIPT_SAFE = ("</", "<\\/")

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Becca call-quality scorecards</title>
<style>
  :root {
    color-scheme: light;
    --page: #f9f9f7; --surface: #fcfcfb;
    --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
    --grid: #e1e0d9; --axis: #c3c2b7; --ring: rgba(11,11,11,0.10);
    --accent: #2a78d6;
    --heat-1: #cde2fb; --heat-2: #86b6ef; --heat-3: #2a78d6;
    --heat-3-ink: #ffffff;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      color-scheme: dark;
      --page: #0d0d0d; --surface: #1a1a19;
      --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
      --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10);
      --accent: #3987e5;
      --heat-1: #0d366b; --heat-2: #184f95; --heat-3: #3987e5;
      --heat-3-ink: #0b0b0b;
    }
  }
  * { box-sizing: border-box; margin: 0; }
  body {
    background: var(--page); color: var(--ink);
    font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
    padding: 32px 24px 64px;
  }
  main { max-width: 1080px; margin: 0 auto; }
  h1 { font-size: 22px; font-weight: 650; }
  .sub { color: var(--ink-2); margin: 4px 0 24px; }
  .card {
    background: var(--surface); border: 1px solid var(--ring);
    border-radius: 10px; padding: 20px; margin-bottom: 20px;
  }
  h2 { font-size: 15px; font-weight: 650; margin-bottom: 12px; }
  /* batch picker */
  .batches { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; }
  .batches button {
    font: inherit; color: var(--ink-2); background: var(--surface);
    border: 1px solid var(--ring); border-radius: 999px;
    padding: 5px 14px; cursor: pointer;
  }
  .batches button:hover { border-color: var(--axis); }
  .batches button.on {
    color: var(--accent); border-color: var(--accent); font-weight: 650;
  }
  /* stat tiles */
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 20px; }
  .kpi { background: var(--surface); border: 1px solid var(--ring); border-radius: 10px; padding: 14px 16px; }
  .kpi .v { font-size: 30px; font-weight: 650; }
  .kpi .l { color: var(--ink-2); font-size: 13px; }
  /* batch bar chart */
  .chart { display: flex; align-items: flex-end; gap: 14px; height: 150px; padding-top: 18px; border-bottom: 1px solid var(--axis); }
  .chart .col { flex: 0 1 72px; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; height: 100%; cursor: pointer; }
  .chart .val { color: var(--ink-2); font-size: 12px; font-variant-numeric: tabular-nums; margin-bottom: 3px; }
  .chart .bar { width: 100%; max-width: 44px; background: var(--accent); border-radius: 4px 4px 0 0; min-height: 2px; opacity: .45; }
  .chart .col.on .bar, .chart .col:hover .bar { opacity: 1; }
  .xlabels { display: flex; gap: 14px; margin-top: 6px; }
  .xlabels span { flex: 0 1 72px; text-align: center; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
  /* tables */
  table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
  th, td { text-align: center; padding: 6px 8px; font-size: 13px; border-bottom: 1px solid var(--grid); }
  th { color: var(--muted); font-weight: 500; }
  th.rowh, td.rowh { text-align: left; color: var(--ink); font-weight: 500; }
  thead th { vertical-align: bottom; }
  td.zero { color: var(--axis); }
  td.h1 { background: var(--heat-1); }
  td.h2 { background: var(--heat-2); }
  td.h3 { background: var(--heat-3); color: var(--heat-3-ink); }
  .tablewrap { overflow-x: auto; }
  /* per-call cards */
  .callmeta { color: var(--muted); font-size: 13px; margin: -8px 0 10px; }
  blockquote {
    border-left: 3px solid var(--axis); color: var(--ink-2);
    padding: 2px 0 2px 12px; margin: 0 0 12px; font-size: 14px;
  }
  .viol { border-top: 1px solid var(--grid); padding: 10px 0; }
  .viol .head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
  .chip {
    font-size: 12px; font-weight: 600; border-radius: 999px; padding: 1px 10px;
    border: 1px solid var(--ring); color: var(--ink-2);
  }
  .chip.judged { color: var(--accent); border-color: var(--accent); }
  .turn { color: var(--muted); font-size: 12px; }
  .note { font-size: 14px; margin-top: 2px; }
  .quote { color: var(--ink-2); font-size: 13px; font-style: italic; margin-top: 4px; overflow-wrap: anywhere; }
  .clean { color: var(--ink-2); }
  details > summary { cursor: pointer; color: var(--ink-2); font-size: 13px; margin-top: 10px; }
</style>
</head>
<body>
<main>
  <h1>Call-quality scorecards</h1>
  <p class="sub" id="sub"></p>
  <div class="batches" id="batches"></div>
  <div class="kpis" id="kpis"></div>
  <div class="card">
    <h2>Total violations by batch</h2>
    <div class="chart" id="chart"></div>
    <div class="xlabels" id="xlabels"></div>
    <details>
      <summary>Per-criterion totals across batches (table)</summary>
      <div class="tablewrap"><table id="crossbatch"></table></div>
    </details>
  </div>
  <div class="card">
    <h2>Violations per call</h2>
    <div class="tablewrap"><table id="matrix"></table></div>
  </div>
  <div id="calls"></div>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
  const BATCHES = JSON.parse(document.getElementById('data').textContent)
    .sort((a, b) => a.generated_at.localeCompare(b.generated_at));
  let current = BATCHES.length - 1;

  const esc = s => String(s).replace(/[&<>"]/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const heat = n => n === 0 ? 'zero' : n === 1 ? 'h1' : n <= 3 ? 'h2' : 'h3';
  const cell = n => `<td class="${heat(n)}">${n === 0 ? '\\u00b7' : n}</td>`;
  const total = b => Object.values(b.totals).reduce((a, n) => a + n, 0);

  function render() {
    const b = BATCHES[current];
    const crits = b.criteria;
    const kind = Object.fromEntries(crits.map(c => [c.id, c.kind]));
    const defn = Object.fromEntries(crits.map(c => [c.id, c.definition]));

    document.getElementById('sub').textContent =
      `${b.batch} \\u00b7 rubric v${b.rubric_version} \\u00b7 ` +
      `${new Date(b.generated_at).toLocaleString()} \\u00b7 ${b.calls.length} calls`;

    document.getElementById('batches').innerHTML = BATCHES.map((x, i) =>
      `<button class="${i === current ? 'on' : ''}" onclick="pick(${i})">${esc(x.batch)}</button>`
    ).join('');

    const mech = b.calls.flatMap(c => c.violations).filter(v => kind[v.criterion] === 'mechanical').length;
    const judged = b.calls.flatMap(c => c.violations).filter(v => kind[v.criterion] === 'judged').length;
    document.getElementById('kpis').innerHTML = [
      [b.calls.length, 'calls scored'],
      [total(b), 'violations'],
      [mech, 'mechanical'],
      [judged, 'judged'],
    ].map(([v, l]) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');

    const max = Math.max(1, ...BATCHES.map(total));
    document.getElementById('chart').innerHTML = BATCHES.map((x, i) => {
      const t = total(x);
      return `<div class="col ${i === current ? 'on' : ''}" onclick="pick(${i})" title="${esc(x.batch)}: ${t}">` +
        `<div class="val">${t}</div><div class="bar" style="height:${Math.round(t / max * 100)}%"></div></div>`;
    }).join('');
    document.getElementById('xlabels').innerHTML =
      BATCHES.map(x => `<span>${esc(x.batch)}</span>`).join('');

    document.getElementById('crossbatch').innerHTML =
      '<thead><tr><th class="rowh">criterion</th>' +
      BATCHES.map(x => `<th>${esc(x.batch)}</th>`).join('') + '</tr></thead><tbody>' +
      crits.map(c => `<tr><td class="rowh" title="${esc(c.definition)}">${c.id}</td>` +
        BATCHES.map(x => cell(x.totals[c.id] ?? 0)).join('') + '</tr>').join('') +
      '</tbody>';

    document.getElementById('matrix').innerHTML =
      '<thead><tr><th class="rowh">call</th>' +
      crits.map(c => `<th title="${esc(c.definition)}">${c.id.replaceAll('_', ' ')}</th>`).join('') +
      '<th>total</th></tr></thead><tbody>' +
      b.calls.map(c => `<tr><td class="rowh">${esc(c.label)}</td>` +
        crits.map(cr => cell(c.counts[cr.id] ?? 0)).join('') +
        `<td>${Object.values(c.counts).reduce((a, n) => a + n, 0)}</td></tr>`).join('') +
      '</tbody>';

    document.getElementById('calls').innerHTML = b.calls.map(c =>
      `<div class="card"><h2>${esc(c.label)} \\u2014 ${esc(c.agent)}</h2>` +
      `<p class="callmeta">${esc(c.source)} \\u00b7 ${esc(c.conversation_model)} \\u00b7 ` +
      `${c.agent_turns} agent turns of ${c.turns}</p>` +
      (c.judge_overall ? `<blockquote>${esc(c.judge_overall)}</blockquote>` : '') +
      (c.violations.length === 0 ? '<p class="clean">No violations.</p>' :
        c.violations.map(v =>
          `<div class="viol"><div class="head">` +
          `<span class="chip ${kind[v.criterion]}" title="${esc(defn[v.criterion])}">${v.criterion}</span>` +
          `<span class="turn">turn ${v.turn} \\u00b7 ${kind[v.criterion]}</span></div>` +
          `<div class="note">${esc(v.note)}</div>` +
          `<div class="quote">\\u201c${esc(v.quote)}\\u201d</div></div>`
        ).join('')) +
      `</div>`
    ).join('');
  }

  function pick(i) { current = i; render(); }
  render();
</script>
</body>
</html>
"""


def index_html(batches: list[dict[str, Any]]) -> str:
    data = json.dumps(batches).replace(*_SCRIPT_SAFE)
    return _PAGE.replace("__DATA__", data)


def write_index(out_dir: Path) -> Path:
    batches = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out_dir.glob("*.json"))]
    path = out_dir / "index.html"
    path.write_text(index_html(batches), encoding="utf-8")
    return path
