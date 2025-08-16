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