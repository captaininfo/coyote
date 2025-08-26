// insights.js — renders the 3 MVP charts in the Overview carousel.
// Dependencies: Chart.js (window.Chart), D3 v7 (window.d3), coyoteRunCypher (from coyote_ui.js)

(() => {
  let newTopicsChart = null;
  let sensemakingChart = null;
  let rhythmsDrawn = false;

  // Utility
  async function getJSON(url) {
    const r = await fetch(url);
    return await r.json();
  }
  function $(sel) { return document.querySelector(sel); }

  window.__insightsResize = (i) => {
  if (i === 0 && newTopicsChart) newTopicsChart.resize();
  if (i === 1 && sensemakingChart) sensemakingChart.resize();
};

  // 1) New Topics This Week (bar)
  async function renderNewTopics() {
    const el = $('#newTopicsChart');
    if (!el) return;
    const res = await getJSON('/api/insights/new-topics?days=7&limit=12');
    if (!res.ok) return showEmpty('#ntw-empty', true);
    const rows = res.data || [];
    if (!rows.length) return showEmpty('#ntw-empty', true);

    showEmpty('#ntw-empty', false);
    $('#ntw-meta').textContent = `${rows.length} topics · last 7 days`;

    const labels = rows.map(r => r.topic);
    const interactions = rows.map(r => r.interactions || 0);

    if (newTopicsChart) newTopicsChart.destroy();
    newTopicsChart = new Chart(el.getContext('2d'), {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'New topics (interactions)',
          data: interactions
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        onClick: (_, elements) => {
          if (!elements?.length) return;
          const i = elements[0].index;
          const topic = labels[i];
          // Deep link into Browse: pages about topic (last 30 days)
          const cypher = `
MATCH (t:WikiDataOntology)
WHERE toLower(t.label) = toLower($label)
MATCH (w:Webpage)-[:HAS_TOPIC]->(t)
WHERE w.timestamp IS NOT NULL AND datetime(w.timestamp) >= datetime() - duration({days:30})
OPTIONAL MATCH (w)-[r]-(m)
RETURN
  [x IN collect(DISTINCT w) + collect(DISTINCT m) | {id:id(x), labels:labels(x), props:properties(x)}] AS nodes,
  [x IN collect(DISTINCT r) | {id:id(x), type:type(x), s:id(startNode(x)), t:id(endNode(x)), props:properties(x)}] AS rels
`;
          if (typeof window.switchSection === 'function') window.switchSection('browse');
          // ensure the graph module booted
          setTimeout(() => window.coyoteRunCypher && window.coyoteRunCypher(cypher, { label: topic }), 250);
        },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: {
            label: ctx => `${ctx.parsed.y} interactions` }
          }
        },
        scales: { y: { beginAtZero: true } }
      }
    });
  }

  // 2) Sensemaking Rate (line 0..1)
  async function renderSensemaking() {
    const el = $('#sensemakingChart');
    if (!el) return;
    const res = await getJSON('/api/insights/sensemaking-rate?days=30&window=30');
    if (!res.ok) return showEmpty('#smr-empty', true);
    const rows = res.data || [];
    if (!rows.length) return showEmpty('#smr-empty', true);

    showEmpty('#smr-empty', false);
    const labels = rows.map(r => r.d);
    const rates   = rows.map(r => r.rate || 0);
    const searches= rows.map(r => r.searches || 0);
    const annos   = rows.map(r => r.annos || 0);
    const avg = (rates.reduce((a,b)=>a+b,0) / (rates.length || 1));
    $('#smr-meta').textContent = `avg ${(avg*100).toFixed(0)}% · ${sum(searches)} searches · ${sum(annos)} annotations`;

    if (sensemakingChart) sensemakingChart.destroy();
    sensemakingChart = new Chart(el.getContext('2d'), {
      type: 'line',
      data: { labels, datasets: [{ label:'Sensemaking rate', data: rates, tension: 0.25, fill: false }]},
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: { y: { min: 0, max: 1, ticks: { callback: v => `${Math.round(v*100)}%` } } },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: {
            label: ctx => `${Math.round(ctx.parsed.y*100)}% (SERP→annotation)`,
            afterBody: ctx => {
              const i = ctx[0].dataIndex;
              return [`searches: ${searches[i]}`, `annotations: ${annos[i]}`];
            }}
          }
        }
      }
    });
    function sum(arr){return arr.reduce((a,b)=>a+(b||0),0)}
  }

  // 3) Learning Rhythms (radial area, D3)
  async function renderRhythms() {
    const svg = d3.select('#rhythmsSvg');
    if (svg.empty()) return;
    const res = await getJSON('/api/insights/rhythms?days=7');
    if (!res.ok) { showEmpty('#lr-empty', true); return; }
    const rows = res.data || [];
    if (!rows.length) { showEmpty('#lr-empty', true); return; }
    showEmpty('#lr-empty', false);
    $('#lr-meta').textContent = `last 7 days · by hour`;

    if (rhythmsDrawn) { svg.selectAll('*').remove(); }
    const data = Array.from({length:24}, (_,h) => {
      const found = rows.find(r => r.hour === h || r.hour === (''+h));
      return { hour: h, value: (found ? Number(found.value) : 0) };
    });

    const box = svg.node().getBoundingClientRect();
    const w = box.width || 600, h = box.height || 280;
    const cx = w/2, cy = h/2, R = Math.min(w,h)/2 - 18;

    const angle = d3.scaleLinear().domain([0,24]).range([0, 2*Math.PI]);
    const radius= d3.scaleLinear().domain([0, d3.max(data, d=>d.value)||1]).range([R*0.2, R]);

    const g = svg.attr('viewBox', [0,0,w,h]).append('g').attr('transform', `translate(${cx},${cy})`);

    // radial area
    const area = d3.areaRadial()
      .curve(d3.curveCardinalClosed)
      .angle(d => angle(d.hour + 0.5))
      .innerRadius(R*0.2)
      .outerRadius(d => radius(d.value));

    g.append('path')
      .datum(data)
      .attr('d', area)
      .attr('fill', '#4A90E230')
      .attr('stroke', '#4A90E2')
      .attr('stroke-width', 1.5);

    // hour ticks
    const ticks = d3.range(0,24,3);
    g.selectAll('.tick')
      .data(ticks)
      .join('text')
      .attr('class','tick')
      .attr('text-anchor','middle')
      .attr('alignment-baseline','middle')
      .attr('x', d => Math.cos(angle(d)-Math.PI/2) * (R+8))
      .attr('y', d => Math.sin(angle(d)-Math.PI/2) * (R+8))
      .style('font-size','10px')
      .text(d => d.toString().padStart(2,'0'));

    rhythmsDrawn = true;
  }

  function showEmpty(sel, show) {
    const el = $(sel);
    if (!el) return;
    el.style.display = show ? 'block' : 'none';
  }

  // Carousel hook from HTML: 0->new topics, 1->sensemaking, 2->rhythms
  async function renderInsightsSlide(index) {
    try {
      if (index === 0) await renderNewTopics();
      else if (index === 1) await renderSensemaking();
      else if (index === 2) await renderRhythms();
    } catch (e) { console.error('renderInsightsSlide error', e); }
  }
  window.renderInsightsSlide = renderInsightsSlide;
})();
