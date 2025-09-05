// coyote_ui.js - Enhanced UI controls with better accessibility

let statusCheckInterval = null;

// Status icons for better accessibility (using Unicode symbols)
const STATUS_ICONS = {
    online: '✓',  // Checkmark
    offline: '✗', // X mark
    unknown: '?', // Question mark
    loading: '⟳'  // Refresh symbol
};

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    console.log('Coyote UI initialized');
    
    // Wire up button click handlers
    setupButtonHandlers();
    
    // Start status monitoring
    checkStatus();
    checkExtensionStatus();
    
    // Check status every 5 seconds
    statusCheckInterval = setInterval(() => {
        checkStatus();
        checkExtensionStatus();
    }, 5000);
});

function setupButtonHandlers() {
    // Docker management buttons (System Status page)
    const btnStartCore = document.getElementById('btnStartCore');
    const btnStartAll = document.getElementById('btnStartAllServices');
    const btnStartLLM = document.getElementById('btnStartLLM');
    const btnStopAll = document.getElementById('btnStopAllServices');
    const btnRestart = document.getElementById('btnRestartServices');
    
    if (btnStartCore) {
        btnStartCore.addEventListener('click', startCoreServices);
    }
    
    if (btnStartAll) {
        btnStartAll.addEventListener('click', startAllServices);
    }
    
    if (btnStartLLM) {
        btnStartLLM.addEventListener('click', startLLMServices);
    }
    
    if (btnStopAll) {
        btnStopAll.addEventListener('click', stopAllServices);
    }
    
    if (btnRestart) {
        btnRestart.addEventListener('click', restartServices);
    }
    
    // Quick action buttons (Overview page if they exist)
    const btnQuickStart = document.querySelector('.btn-quick-start');
    const btnQuickStop = document.querySelector('.btn-quick-stop');
    
    if (btnQuickStart) {
        btnQuickStart.addEventListener('click', startCoreServices);
    }
    
    if (btnQuickStop) {
        btnQuickStop.addEventListener('click', stopAllServices);
    }
}

// ============== Readiness-gated “Open Coyote Search” ==============
// This function name is referenced inline in coyote_wireframe.html
async function openCoyoteSearch() {
  try {
    const readiness = await getReadiness();
    if (readiness.ready) {
      // Everything required is present
      window.open('/static/new_tab.html', '_blank'); // change to /static/html/new_tab.html if that’s your path
      return false; // prevent "#" navigation
    }
    showSetupModal(readiness);
    return false; // prevent "#" navigation
  } catch (err) {
    console.error('Open Coyote Search readiness failed:', err);
    showStatus('Could not verify system readiness. Open the System Status tab to investigate.', 'warning');
    if (typeof window.switchSection === 'function') {
      window.switchSection('status');
    }
    return false; // prevent "#" navigation
  }
}
// Expose for inline onclick in HTML
window.openCoyoteSearch = openCoyoteSearch;

async function getReadiness() {
  const [statusResp, extResp] = await Promise.allSettled([
    fetch('/api/status'),
    fetch('/extension_status')
  ]);

  let services = [];
  let dockerOk = false;
  if (statusResp.status === 'fulfilled') {
    const data = await statusResp.value.json();
    dockerOk = (data.status === 'success');
    services = Array.isArray(data.services) ? data.services : [];
  }

  let extensionActive = false;
  if (extResp.status === 'fulfilled') {
    const ext = await extResp.value.json();
    extensionActive = !!ext.active;
  }

  const isUp = (s) => s.State === 'running' || ((s.Status || '').includes('Up'));
  const byName = (s) => (s.Name || s.Service || '').toLowerCase();

  let coyoteCoreUp = false;
  let neo4jUp = false;
  for (const s of services) {
    const n = byName(s);
    if ((n.includes('coyote_app') || n.includes('coyote-coyote_app-1')) && isUp(s)) coyoteCoreUp = true;
    if ((n.includes('database')   || n.includes('coyote-database-1'))   && isUp(s)) neo4jUp = true;
  }

  return {
    ready: dockerOk && coyoteCoreUp && neo4jUp && extensionActive,
    dockerOk, coyoteCoreUp, neo4jUp, extensionActive
  };
}

function showSetupModal(r) {
  const old = document.getElementById('coyote-setup-modal');
  if (old) old.remove();

  const missing = [];
  if (!r.dockerOk) missing.push('Docker is not available');
  if (r.dockerOk && !r.coyoteCoreUp) missing.push('Coyote Core container is not running');
  if (r.dockerOk && !r.neo4jUp) missing.push('Neo4j database container is not running');
  if (!r.extensionActive) missing.push('Browser extension is not active');

  const el = document.createElement('div');
  el.id = 'coyote-setup-modal';
  el.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:2000;display:flex;align-items:center;justify-content:center;padding:20px;';
  el.innerHTML = `
    <div role="dialog" aria-modal="true" aria-labelledby="coyote-setup-title"
         style="background:#fff;max-width:640px;width:100%;border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.2);">
      <div style="padding:16px 20px;border-bottom:1px solid #eee;display:flex;justify-content:space-between;align-items:center;">
        <h2 id="coyote-setup-title" style="margin:0;font-size:18px;">Before we open Coyote Search…</h2>
        <button aria-label="Close" style="background:none;border:none;font-size:20px;cursor:pointer;">✕</button>
      </div>
      <div style="padding:16px 20px;">
        <p>Coyote needs <strong>both</strong> the browser extension and the core Docker services to be running.</p>
        <ul style="margin-left:18px;">${missing.map(m => `<li>${m}</li>`).join('')}</ul>
        <p style="margin-top:12px;">What to do next:</p>
        <ol style="margin-left:18px;">
          <li>Open the <em>System Status</em> tab and click <strong>Start Core</strong> (or <strong>Start Everything</strong>).</li>
          <li>If the extension shows “Offline”, follow the <em>Setup Guide</em> to load the temporary add-on in Firefox.</li>
        </ol>
        <p style="font-size:12px;color:#555;margin-top:12px;">Tip: After starting services, the indicators should turn green within ~10s.</p>
      </div>
      <div style="padding:12px 20px;border-top:1px solid #eee;display:flex;gap:8px;justify-content:flex-end;">
        <button id="coyote-setup-open-status" class="btn btn-secondary">Go to Status</button>
        <button id="coyote-setup-open-setup"  class="btn btn-secondary">Open Setup Guide</button>
        <button id="coyote-setup-retry"       class="btn btn-primary">Retry</button>
      </div>
    </div>`;

  const closeBtn = el.querySelector('button[aria-label="Close"]');
  closeBtn.onclick = () => el.remove();
  el.querySelector('#coyote-setup-open-status').onclick = () => {
    if (typeof window.switchSection === 'function') window.switchSection('status');
    el.remove();
  };
  el.querySelector('#coyote-setup-open-setup').onclick = () => {
    if (typeof window.switchSection === 'function') window.switchSection('setup');
    el.remove();
  };
  el.querySelector('#coyote-setup-retry').onclick = async () => {
    el.remove();
    await openCoyoteSearch();
  };
  document.body.appendChild(el);
}

async function startCoreServices() {
    console.log('Starting core services...');
    showStatus('Starting core services (Neo4j + Coyote Core)...', 'info');
    
    try {
        // Increase timeout for start operations as they can take time
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 30000); // 30 second timeout
        
        const response = await fetch('/api/start-core', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            signal: controller.signal
        });
        
        clearTimeout(timeoutId);
        
        const data = await response.json();
        
        if (data.status === 'success') {
            showStatus('Core services started successfully!', 'success');
            console.log('Output:', data.stdout);
        } else {
            showStatus('Failed to start core services. Check logs for details.', 'error');
            console.error('Error:', data.stderr);
            showErrorDetails(data.stderr);
        }
        
        // Refresh status after a moment
        setTimeout(checkStatus, 2000);
        
    } catch (error) {
        if (error.name === 'AbortError') {
            showStatus('Start operation is taking longer than expected. Services may still be starting...', 'warning');
            // Still check status after a delay
            setTimeout(checkStatus, 5000);
        } else {
            showStatus('Failed to connect to UI server: ' + error.message, 'error');
        }
        console.error('Error:', error);
    }
}

async function startLLMServices() {
    console.log('Starting LLM services...');
    showStatus('Starting LLM services (Ollama)...', 'info');
    
    try {
        const response = await fetch('/api/start-llm', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'}
        });
        
        const data = await response.json();
        
        if (data.status === 'success') {
            showStatus('LLM services started successfully!', 'success');
            console.log('Output:', data.stdout);
        } else {
            showStatus('Failed to start LLM services. Check logs.', 'error');
            console.error('Error:', data.stderr);
            showErrorDetails(data.stderr);
        }
        
        setTimeout(checkStatus, 2000);
        
    } catch (error) {
        showStatus('Failed to connect to UI server: ' + error.message, 'error');
        console.error('Error:', error);
    }
}

async function startAllServices() {
    console.log('Starting all services...');
    showStatus('Starting all services (this may take several minutes to pull images)...', 'info');
    
    try {
        // Very long timeout for pulling all images
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 300000); // 5 minute timeout
        
        const response = await fetch('/api/start-all', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            signal: controller.signal
        });
        
        clearTimeout(timeoutId);
        
        const data = await response.json();
        
        if (data.status === 'success') {
            showStatus('All services started successfully!', 'success');
            console.log('Output:', data.stdout);
        } else {
            showStatus('Failed to start services. Check the error details below.', 'error');
            console.error('Error:', data.stderr);
            showErrorDetails(data.stderr);
        }
        
        // Refresh status after a moment
        setTimeout(checkStatus, 2000);
        
    } catch (error) {
        if (error.name === 'AbortError') {
            showStatus('Start operation is taking longer than expected. Large images may still be downloading...', 'warning');
            setTimeout(checkStatus, 10000);
        } else {
            showStatus('Failed to connect to UI server: ' + error.message, 'error');
        }
        console.error('Error:', error);
    }
}

async function stopAllServices() {
    console.log('Stopping all services...');
    showStatus('Stopping all services (verifying shutdown)...', 'info');
    
    try {
        // Longer timeout for stop operations as they may need to force cleanup
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 60000); // 60 second timeout
        
        const response = await fetch('/api/stop', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            signal: controller.signal
        });
        
        clearTimeout(timeoutId);
        
        const data = await response.json();
        
        if (data.status === 'success') {
            showStatus('All services stopped successfully!', 'success');
            console.log('Output:', data.stdout);
        } else if (data.status === 'partial') {
            showStatus('Some services may still be running. Try Force Cleanup if needed.', 'warning');
            console.warn('Partial stop:', data.message);
        } else {
            showStatus('Failed to stop services: ' + (data.message || data.stderr), 'error');
            console.error('Error:', data.stderr);
        }
        
        // Refresh status after a moment
        setTimeout(checkStatus, 2000);
        
    } catch (error) {
        if (error.name === 'AbortError') {
            showStatus('Stop operation timed out. Services may still be stopping. Try Force Cleanup.', 'warning');
        } else {
            showStatus('Failed to connect to UI server: ' + error.message, 'error');
        }
        console.error('Error:', error);
    }
}

async function restartServices() {
    console.log('Restarting services...');
    showStatus('Restarting services...', 'info');
    
    // Stop then start
    await stopAllServices();
    setTimeout(() => {
        startAllServices();
    }, 3000);
}

async function checkStatus() {
    try {
        const response = await fetch('/api/status');
        const data = await response.json();
        
        if (data.status === 'success') {
            updateStatusDisplay(data.services);
        } else {
            console.error('Status check failed:', data.message);
            updateStatusDisplay([]);
        }
        
    } catch (error) {
        console.error('Failed to check status:', error);
        updateStatusDisplay([]);
    }
}

async function checkExtensionStatus() {
    try {
        const response = await fetch('/extension_status');
        const data = await response.json();
        
        updateExtensionStatus(data.active);
        
    } catch (error) {
        console.error('Failed to check extension status:', error);
        updateExtensionStatus(false);
    }
}

function updateExtensionStatus(isActive) {
    const element = document.querySelector('[data-component="browser_extension"]');
    if (element) {
        // Update with accessible status indicator
        const statusText = isActive ? 'Online' : 'Offline';
        const statusIcon = isActive ? STATUS_ICONS.online : STATUS_ICONS.offline;
        const statusClass = isActive ? 'status-online' : 'status-offline';
        
        element.className = `status-indicator ${statusClass}`;
        element.innerHTML = `
            <span class="status-icon" aria-hidden="true">${statusIcon}</span>
            <span class="status-text">${statusText}</span>
        `;
        element.setAttribute('aria-label', `Browser Extension: ${statusText}`);
    }
}

function updateStatusDisplay(services) {
    // Map of service names to UI component names
    const serviceMap = {
        'coyote-database-1': 'neo4j_database',
        'coyote-coyote_app-1': 'coyote_core',
        'coyote-llm-1': 'ollama_llm',
        'coyote-bot-1': 'coyote_agent_bot',
        // Also check by service name (without project prefix)
        'database': 'neo4j_database',
        'coyote_app': 'coyote_core',
        'llm': 'ollama_llm',
        'bot': 'coyote_agent_bot'
    };
    
    // Reset all status indicators with accessible defaults
    const allComponents = ['neo4j_database', 'coyote_core', 'ollama_llm', 'coyote_agent_bot'];
    allComponents.forEach(comp => {
        const element = document.querySelector(`[data-component="${comp}"]`);
        if (element) {
            element.className = 'status-indicator status-offline';
            element.innerHTML = `
                <span class="status-icon" aria-hidden="true">${STATUS_ICONS.offline}</span>
                <span class="status-text">Offline</span>
            `;
            element.setAttribute('aria-label', `${comp.replace(/_/g, ' ')}: Offline`);
        }
    });
    
    // Update based on running services
    services.forEach(service => {
        // Check both Name and Service fields
        const serviceName = service.Name || service.Service || '';
        const componentName = serviceMap[serviceName];
        
        if (componentName) {
            const element = document.querySelector(`[data-component="${componentName}"]`);
            if (element) {
                // Check if service is running
                const isRunning = service.State === 'running' || 
                                (service.Status && service.Status.includes('Up'));
                
                const statusText = isRunning ? 'Online' : 'Offline';
                const statusIcon = isRunning ? STATUS_ICONS.online : STATUS_ICONS.offline;
                const statusClass = isRunning ? 'status-online' : 'status-offline';
                
                element.className = `status-indicator ${statusClass}`;
                element.innerHTML = `
                    <span class="status-icon" aria-hidden="true">${statusIcon}</span>
                    <span class="status-text">${statusText}</span>
                `;
                element.setAttribute('aria-label', `${componentName.replace(/_/g, ' ')}: ${statusText}`);
            }
        }
    });
    
    // Update overall Docker status
    const dockerStatus = document.querySelector('[data-component="docker"]');
    if (dockerStatus) {
        const isOnline = services.length > 0;
        dockerStatus.className = `status-indicator ${isOnline ? 'status-online' : 'status-offline'}`;
        dockerStatus.innerHTML = `
            <span class="status-icon" aria-hidden="true">${isOnline ? STATUS_ICONS.online : STATUS_ICONS.offline}</span>
            <span class="status-text">${isOnline ? 'Online' : 'Offline'}</span>
        `;
    }
    
    // Update status counts
    const runningCount = services.filter(s => 
        s.State === 'running' || (s.Status && s.Status.includes('Up'))
    ).length;
    
    const statusSummary = document.getElementById('status-summary');
    if (statusSummary) {
        statusSummary.textContent = `${runningCount} of ${services.length} services running`;
    }
}

function showStatus(message, type = 'info') {
    // Find or create status message area
    let statusArea = document.getElementById('status-messages');
    
    if (!statusArea) {
        // Create it in the main content area if it doesn't exist
        statusArea = document.createElement('div');
        statusArea.id = 'status-messages';
        statusArea.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 1000;
            max-width: 400px;
        `;
        statusArea.setAttribute('role', 'status');
        statusArea.setAttribute('aria-live', 'polite');
        document.body.appendChild(statusArea);
    }
    
    if (statusArea) {
        const alert = document.createElement('div');
        alert.className = `alert alert-${type}`;
        alert.setAttribute('role', 'alert');
        alert.style.cssText = `
            padding: 15px;
            margin-bottom: 10px;
            border-radius: 4px;
            background: ${type === 'error' ? '#f8d7da' : type === 'success' ? '#d4edda' : '#d1ecf1'};
            color: ${type === 'error' ? '#721c24' : type === 'success' ? '#155724' : '#0c5460'};
            border: 1px solid ${type === 'error' ? '#f5c6cb' : type === 'success' ? '#c3e6cb' : '#bee5eb'};
        `;
        alert.textContent = message;
        
        statusArea.appendChild(alert);
        
        // Auto-remove after 10 seconds (longer for errors)
        setTimeout(() => {
            alert.remove();
        }, type === 'error' ? 10000 : 5000);
    }
    
    console.log(`[${type.toUpperCase()}] ${message}`);
}

function showErrorDetails(errorText) {
    if (!errorText) return;
    
    // Create expandable error details area
    let errorArea = document.getElementById('error-details');
    if (!errorArea) {
        errorArea = document.createElement('div');
        errorArea.id = 'error-details';
        errorArea.style.cssText = `
            position: fixed;
            bottom: 20px;
            right: 20px;
            max-width: 600px;
            max-height: 300px;
            overflow-y: auto;
            background: #fff;
            border: 2px solid #e74c3c;
            border-radius: 4px;
            padding: 15px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            z-index: 999;
        `;
        
        const header = document.createElement('div');
        header.style.cssText = 'display: flex; justify-content: space-between; margin-bottom: 10px;';
        header.innerHTML = `
            <strong>Error Details:</strong>
            <button onclick="document.getElementById('error-details').remove()" 
                    style="background: none; border: none; cursor: pointer; font-size: 20px;"
                    aria-label="Close error details">✕</button>
        `;
        
        const content = document.createElement('pre');
        content.style.cssText = 'font-size: 12px; overflow-x: auto;';
        content.textContent = errorText;
        
        errorArea.appendChild(header);
        errorArea.appendChild(content);
        document.body.appendChild(errorArea);
        
        // Auto-remove after 30 seconds
        setTimeout(() => {
            if (document.getElementById('error-details')) {
                document.getElementById('error-details').remove();
            }
        }, 30000);
    }
}

// ============== Explore Visually (Cytoscape) ==============
(() => {
  let cy = null;
  let inited = false;
  const DEFAULTS = { seedLimit: 60, nodeLimit: 120, relLimit: 240 };

  const CANNED = [
    // id, label, description, cypher
    {
      id: "recent_activity",
      label: "Recent activity (default)",
      desc: "Newest Webpages/Annotations/Purposes/SearchTerms + 1-hop",
      cypher: `
CALL {
  MATCH (n)
  WHERE (n:Webpage OR n:Annotation OR n:Purpose OR n:SearchTerms)
    AND n.timestamp IS NOT NULL
  RETURN n
  ORDER BY datetime(n.timestamp) DESC
  LIMIT $seedLimit
}
WITH collect(n) AS seeds
UNWIND seeds AS s
OPTIONAL MATCH (s)-[r]-(m)
WITH collect(DISTINCT s) AS sN, collect(DISTINCT m) AS mN, collect(DISTINCT r) AS rs
WITH sN + mN AS nodes, rs AS rels
RETURN
  [x IN nodes | {id:id(x), labels:labels(x), props:properties(x)}] AS nodes,
  [x IN rels  | {id:id(x), type:type(x), s:id(startNode(x)), t:id(endNode(x)), props:properties(x)}] AS rels
`    },
    {
      id: "latest_webpages_30d",
      label: "Latest webpages (30 days)",
      desc: "Recent Webpage nodes and their topics/annotations",
      cypher: `
CALL {
  MATCH (w:Webpage)
  WHERE w.timestamp IS NOT NULL AND datetime(w.timestamp) >= datetime() - duration({days:30})
  RETURN w ORDER BY datetime(w.timestamp) DESC LIMIT $seedLimit
}
WITH collect(w) AS ws
UNWIND ws AS w
OPTIONAL MATCH (w)-[r]-(m)
RETURN
  [x IN collect(DISTINCT w) + collect(DISTINCT m) | {id:id(x), labels:labels(x), props:properties(x)}] AS nodes,
  [x IN collect(DISTINCT r) | {id:id(x), type:type(x), s:id(startNode(x)), t:id(endNode(x)), props:properties(x)}] AS rels
`    },
    {
      id: "topic_cloud",
      label: "Top topics (30 days)",
      desc: "Frequently used WikiDataOntology topics and connected pages",
      cypher: `
CALL {
  MATCH (w:Webpage)-[:HAS_TOPIC]->(t:WikiDataOntology)
  WHERE w.timestamp IS NOT NULL AND datetime(w.timestamp) >= datetime() - duration({days:30})
  RETURN t, count(*) AS c ORDER BY c DESC LIMIT $seedLimit
}
WITH collect(t) AS ts
UNWIND ts AS t
OPTIONAL MATCH (t)<-[r:HAS_TOPIC]-(n)
RETURN
  [x IN collect(DISTINCT t) + collect(DISTINCT n) | {id:id(x), labels:labels(x), props:properties(x)}] AS nodes,
  [x IN collect(DISTINCT r) | {id:id(x), type:type(x), s:id(startNode(x)), t:id(endNode(x)), props:properties(x)}] AS rels
`    },
    {
      id: "annotations_recent",
      label: "Recent annotations → pages",
      desc: "Latest Annotation nodes and their source Webpages",
      cypher: `
CALL {
  MATCH (a:Annotation)
  WHERE a.timestamp IS NOT NULL
  RETURN a ORDER BY datetime(a.timestamp) DESC LIMIT $seedLimit
}
WITH collect(a) AS as
UNWIND as AS a
OPTIONAL MATCH (w:Webpage)-[r:HAS_ANNOTATION]->(a)
RETURN
  [x IN collect(DISTINCT a) + collect(DISTINCT w) | {id:id(x), labels:labels(x), props:properties(x)}] AS nodes,
  [x IN collect(DISTINCT r) | {id:id(x), type:type(x), s:id(startNode(x)), t:id(endNode(x)), props:properties(x)}] AS rels
`    },
    {
      id: "purposes_to_search",
      label: "Purposes → Search terms (30 days)",
      desc: "Recent Purpose nodes and initiated SearchTerms",
      cypher: `
MATCH (p:Purpose)-[r:INITIATES_SEARCH]->(s:SearchTerms)
WHERE p.timestamp IS NOT NULL AND datetime(p.timestamp) >= datetime() - duration({days:30})
RETURN
  [x IN collect(DISTINCT p) + collect(DISTINCT s) | {id:id(x), labels:labels(x), props:properties(x)}] AS nodes,
  [x IN collect(DISTINCT r) | {id:id(x), type:type(x), s:id(startNode(x)), t:id(endNode(x)), props:properties(x)}] AS rels
`    }
  ];

  function buildCannedMenu() {
    const sel = document.getElementById('cg-canned');
    if (!sel) return;
    CANNED.forEach(q => {
      const opt = document.createElement('option');
      opt.value = q.id;
      opt.textContent = `📎 ${q.label}`;
      opt.title = q.desc;
      sel.appendChild(opt);
    });
  }

  function setStatus(msg, kind='info') {
    const el = document.getElementById('cg-status');
    if (!el) return;
    const colors = { info:'#7f8c8d', success:'#155724', warn:'#856404', error:'#721c24' };
    el.style.color = colors[kind] || colors.info;
    el.textContent = msg;
  }

  function ensureCy() {
    if (cy) return;
    const el = document.getElementById('graphCanvas');
    if (!el) return;
    cy = cytoscape({
      container: el,
      style: [
        { selector: 'node',
          style: {
            'content': 'data(label)',
            'font-size': 10,
            'background-color': '#4A90E2',
            'color': '#1b1f23',
            'text-outline-color': '#ffffff',
            'text-outline-width': 2,
            'text-valign': 'center',
            'text-halign': 'center',
            'width': 'mapData(size, 10, 60, 20, 50)',
            'height': 'mapData(size, 10, 60, 20, 50)'
        }},
        { selector: 'edge',
          style: {
            'width': 1.5,
            'line-color': '#bdc3c7',
            'target-arrow-shape': 'triangle',
            'target-arrow-color': '#bdc3c7',
            'curve-style': 'bezier'
        }},
        { selector: '.Webpage', style: {'background-color':'#4A90E2'} },
        { selector: '.Annotation', style: {'background-color':'#27ae60'} },
        { selector: '.Purpose', style: {'background-color':'#f39c12'} },
        { selector: '.SearchTerms', style: {'background-color':'#9b59b6'} },
        { selector: '.WikiDataOntology', style: {'background-color':'#e74c3c'} }
      ],
      layout: { name: 'cose', animate: false }
    });

    // Basic UX
    cy.on('tap', 'node', (e) => {
      const d = e.target.data();
      setStatus(`${d.labels?.[0] || 'Node'} — ${d.title || d.text || d.url || d.label || d.id}`);
    });
    window.addEventListener('resize', () => cy.resize());
  }

  function toElements(payload) {
    // payload.elements can be array or {nodes, edges}
    if (!payload) return [];
    if (Array.isArray(payload)) return payload;
    const outs = [];
    (payload.nodes || []).forEach(n => {
      const id = `n${n.id}`;
      // pick best label text
      const p = n.props || {};
      const display = p.title || p.annotation_text || p.text || p.url || p.label || `#${n.id}`;
      outs.push({
        group: 'nodes',
        data: {
          id,
          label: display,
          labels: n.labels || [],
          size: Math.min(60, Math.max(10, (display?.length || 10))),
          title: p.title, text: p.text, url: p.url
        },
        classes: (n.labels || []).join(' ')
      });
    });
    (payload.rels || payload.edges || []).forEach(r => {
      outs.push({
        group: 'edges',
        data: {
          id: `e${r.id}`,
          source: `n${r.s}`,
          target: `n${r.t}`,
          label: r.type
        }
      });
    });
    return outs;
  }

  async function fetchJSON(url, opts) {
    const resp = await fetch(url, opts);
    return await resp.json();
  }

  async function loadRecent() {
    ensureCy();
    setStatus('Loading recent activity…');
    try {
      const data = await fetchJSON(`/api/graph/recent?seedLimit=${DEFAULTS.seedLimit}&nodeLimit=${DEFAULTS.nodeLimit}&relLimit=${DEFAULTS.relLimit}`);
      if (data.status !== 'success') throw new Error(data.message || 'Unknown error');
      cy.elements().remove();
      cy.add(toElements(data));
      cy.layout({ name: 'cose', animate:false }).run();
      setStatus(`Loaded ${data.counts?.nodes || cy.nodes().length} nodes / ${data.counts?.rels || cy.edges().length} relationships.`, 'success');
    } catch (e) {
      console.error(e);
      setStatus(`Failed to load recent activity: ${e.message}`, 'error');
    }
  }

  function cannedById(id) { return CANNED.find(q => q.id === id); }

  async function runCanned() {
    const sel = document.getElementById('cg-canned');
    const chosen = cannedById(sel?.value);
    if (!chosen) return setStatus('Pick a quick query first.');
    await runCypher(chosen.cypher);
  }

  async function runCypher(cypher, params = {}) {
    ensureCy();
    setStatus('Running query…');
    try {
      const data = await fetchJSON('/api/graph/run', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ cypher, params: { seedLimit: DEFAULTS.seedLimit, ...params } })
      });
      if (data.status !== 'success') throw new Error(data.message || data.error || 'Query failed');
      cy.elements().remove();
      cy.add(toElements(data));
      cy.layout({ name: 'cose', animate:false }).run();
      setStatus(`Query OK — ${data.counts?.nodes || cy.nodes().length} nodes / ${data.counts?.rels || cy.edges().length} edges.`, 'success');
    } catch (e) {
      console.error(e);
      setStatus(`Query error: ${e.message}`, 'error');
    }
  }

  // NEW: export to other modules (Insights can deep-link into graph)
  window.coyoteRunCypher = runCypher;

  async function askNL() {
    const txt = document.getElementById('cg-nl')?.value.trim();
    if (!txt) return setStatus('Type a natural language question first.');
    setStatus('Translating with LLM → Cypher and running…');
    try {
      const data = await fetchJSON('/api/graph/run', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ nl: txt })
      });
      if (data.status === 'unavailable') {
        return setStatus(data.message || 'LLM unavailable. Start the LLM service or use Quick Cypher.', 'warn');
      }
      if (data.status !== 'success') throw new Error(data.message || data.error || 'NL→Cypher failed');
      cy.elements().remove();
      cy.add(toElements(data));
      cy.layout({ name: 'cose', animate:false }).run();
      setStatus(`Answer loaded — ${data.counts?.nodes || cy.nodes().length} nodes / ${data.counts?.rels || cy.edges().length} edges.`, 'success');
    } catch (e) {
      console.error(e);
      setStatus(`NL query error: ${e.message}`, 'error');
    }
  }

  async function initToolbar() {
    // Wire controls
    const runBtn = document.getElementById('cg-run-canned');
    const askBtn = document.getElementById('cg-ask');
    const resetBtn = document.getElementById('cg-reset');
    runBtn && runBtn.addEventListener('click', runCanned);
    askBtn && askBtn.addEventListener('click', askNL);
    resetBtn && resetBtn.addEventListener('click', loadRecent);

    // Neo4j Browser link
    try {
      const info = await fetchJSON('/api/neo4j-browser-url');
      const a = document.getElementById('cg-browser');
      if (a && info.url) a.href = info.url;
    } catch {}
  }

  async function boot() {
    if (inited) return;
    inited = true;
    buildCannedMenu();
    await initToolbar();
    await loadRecent();
  }

  async function loadChatAssistant() {
  const holder = document.getElementById('chat-frame-holder');
  const frame  = document.getElementById('botFrame');
  const ph     = document.getElementById('chat-placeholder');
  const health = document.getElementById('chat-health');

  // Is the bot service up?
  let botOk = false;
  try {
    const st = await fetch('/api/health-check/bot').then(r => r.json());
    botOk = !!st.running;
    health.textContent = botOk ? '· Online' : '· Offline';
    health.style.color = botOk ? '#155724' : '#721c24';
  } catch { health.textContent = '· Unknown'; }

  if (!botOk) {
    // Suggest starting services; reuse your existing buttons on Status page
    ph.innerHTML = `
      <div style="text-align:center;padding:20px;">
        <p style="margin-bottom:6px;">Assistant is offline.</p>
        <button class="btn btn-primary" onclick="switchSection('status')">Open System Status</button>
      </div>
    `;
    frame.style.display = 'none';
    return;
  }

  // Get the URL and show the frame
  try {
    const info = await fetch('/api/bot-url').then(r => r.json());
    frame.src = info.url || 'http://localhost:8501/?embed=true';
    frame.onload = () => {
      ph.style.display = 'none';
      frame.style.display = 'block';
    };
  } catch {
    ph.innerHTML = `<div style="text-align:center;padding:20px;">
        <p>Could not load assistant frame.</p>
        <button class="btn" onclick="switchSection('status')">Troubleshoot</button>
      </div>`;
    frame.style.display = 'none';
  }
}
window.loadChatAssistant = loadChatAssistant;


  // Expose an init hook that the HTML will call when switching sections
  window.coyoteLoadExplore = boot;
})();
