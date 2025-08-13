// coyote_ui.js - UI JavaScript that works with standalone architecture
// ============ SYSTEM STATUS MONITORING ============

const UI_SERVER = window.location.origin;  // UI server (not Docker)
const DOCKER_CHECK_INTERVAL = 5000;
let statusCheckTimer = null;

// System status state
let systemStatus = {
    docker: false,
    coyote: false,
    agent_bot: false,
    neo4j: false,
    ollama: false,
    extension: false
};

/**
 * Check if browser extension is active via localStorage
 */
function checkExtensionViaLocalStorage() {
    try {
        const stored = localStorage.getItem('coyote_extension_status');
        if (!stored) return false;

        const status = JSON.parse(stored);
        const age = Date.now() - status.timestamp;

        // Consider extension active if heartbeat is less than 15 seconds old
        return age < 15000 && status.active;

    } catch (e) {
        console.error('Error checking extension status:', e);
        return false;
    }
}

/**
 * Comprehensive system check
 */
async function checkSystemStatus() {
    try {
        // 1. Check Docker and containers
        const dockerResponse = await fetch(`/check_docker_status`);
        const dockerData = await dockerResponse.json();

        systemStatus.docker = dockerData.docker_available;
        systemStatus.coyote = dockerData.containers?.coyote_core || false;
        systemStatus.neo4j = dockerData.containers?.neo4j || false;
        systemStatus.agent_bot = dockerData.containers?.coyote_agent_bot || false; 
        systemStatus.ollama = dockerData.containers?.ollama_llm || false;

        // 2. Check browser extension via the UI heartbeat endpoint
        try {
            const extResp = await fetch(`/extension_status`);
            const extData = await extResp.json();
            systemStatus.extension = !!extData.active;
            } catch {
            systemStatus.extension = false;
            }

        // Update UI
        updateStatusDisplay();

        return systemStatus;

    } catch (error) {
        console.error('System status check failed:', error);
        // Assume everything is offline
        systemStatus = { 
            docker: false, 
            coyote: false, 
            neo4j: false, 
            agent_bot: false,
            ollama: false,
            extension: false };
        updateStatusDisplay();
        return systemStatus;
    }
}

/**
 * Update status indicators in the UI
 */
function updateStatusDisplay() {
    // Update status dots
    const indicators = {
        'neo4j_database': systemStatus.neo4j,
        'coyote_core': systemStatus.coyote,
        'coyote_agent_bot': systemStatus.agent_bot,
        'ollama_llm': systemStatus.ollama,
        'browser_extension': systemStatus.extension
    };

    Object.entries(indicators).forEach(([component, isOnline]) => {
        const element = document.querySelector(`[data-component="${component}"]`);
        if (element) {
            element.className = isOnline ? 'status-value status-online' : 'status-value status-offline';
            element.textContent = '●';
        }
    });

    // Debug: Check if elements are found
    console.log('Neo4j element:', document.querySelector('[data-component="neo4j_database"]'));
    console.log('Coyote element:', document.querySelector('[data-component="coyote_core"]'));
    console.log('Agent/Bot element:', document.querySelector('[data-component="coyote_agent_bot"]'));
    console.log('Ollama element:', document.querySelector('[data-component="ollama_llm"]'));
    console.log('Extension element:', document.querySelector('[data-component="browser_extension"]'));

    // Update warnings
    updateWarnings();
}

/**
 * Show appropriate warnings/guidance
 */
function updateWarnings() {
    const warnings = [];

    if (!systemStatus.docker) {
        warnings.push({
            level: 'critical',
            message: 'Docker is not running. Please start Docker Desktop.',
            action: 'Start Docker'
        });
    } else {
        if (!systemStatus.coyote || !systemStatus.neo4j) {
            warnings.push({
                level: 'warning',
                message: 'Coyote containers are not running.',
                action: 'Start Containers',
                callback: startDockerContainers
            });
        }
    }

    if (!systemStatus.extension) {
        warnings.push({
            level: 'warning',
            message: 'Browser extension is not active.',
            action: 'Setup Extension',
            callback: () => showExtensionSetup()
        });
    }

    // Display warnings in UI
    const warningContainer = document.getElementById('system-warnings');
    if (warningContainer) {
        if (warnings.length > 0) {
            warningContainer.innerHTML = warnings.map(w => `
                <div class="warning-item ${w.level}">
                    <span>${w.message}</span>
                    ${w.callback ? `<button onclick="${w.callback.name}()">${w.action}</button>` : ''}
                </div>
            `).join('');
            warningContainer.style.display = 'block';
        } else {
            warningContainer.style.display = 'none';
        }
    }
}

/**
 * Enhanced Open Coyote Search
 */
async function openCoyoteSearch() {
    // Check current status
    await checkSystemStatus();

    const canProceed = systemStatus.coyote && systemStatus.neo4j && systemStatus.extension;

    if (canProceed) {
        // Everything ready - open search
        window.open('/html/new_tab.html', '_blank');
    } else {
        // Show what needs to be done
        let message = 'Please complete setup:\n\n';

        if (!systemStatus.docker) {
            message += '1. Start Docker Desktop\n';
        } else if (!systemStatus.coyote || !systemStatus.neo4j) {
            message += '1. Start Coyote containers\n';
        }

        if (!systemStatus.extension) {
            message += '2. Load the browser extension\n';
        }

        alert(`${message}
            Tip: In Firefox, you can load the extension via:
            1) about:debugging#/runtime/this-firefox
            2) "Load Temporary Add-on" -> select manifest.json
            Once loaded, leave this UI open. The extension light should turn green within ~5–10s.`);

        // Switch to appropriate setup section
        if (!systemStatus.docker || !systemStatus.coyote) {
            switchSection('status');  // Show system status page with Start button
        } else if (!systemStatus.extension) {
            showExtensionSetup();
        }
    }
}

/**
 * Start Docker containers from UI
 */
async function startDockerContainers(btnEl) {
    // Support both inline onclick and addEventListener usage
    const button = btnEl || document.getElementById('btnStartAllServices');
    button.disabled = true;
    button.textContent = 'Starting...';

    try {
        const response = await fetch(`/start_docker_containers`, {
            method: 'POST'
        });
        const data = await response.json();

        if (data.status === 'success') {
            alert('Containers starting... This may take a minute.');
            // Check status after a delay
            setTimeout(() => checkSystemStatus(), 5000);
        } else {
            alert('Failed to start containers: ' + data.message);
        }

    } catch (error) {
        alert('Error starting containers: ' + error);
    } finally {
        button.disabled = false;
        button.textContent = 'Start All Services';
    }
}

async function stopDockerContainers(btnEl) {
    const button = btnEl || document.getElementById('btnStopAllServices');
    button.disabled = true;
    button.textContent = 'Stopping...';
    try {
        const resp = await fetch(`/stop_docker_containers`, { method: 'POST' });
        const data = await resp.json();
        if (data.status === 'success') {
            alert('Containers stopping...');
            setTimeout(() => checkSystemStatus(), 4000);
        } else {
            alert('Failed to stop containers: ' + (data.message || data.error));
        }
    } catch (e) {
        alert('Error stopping containers: ' + e);
    } finally {
        button.disabled = false;
        button.textContent = 'Stop All Services';
    }
}

async function restartDockerContainers(btnEl) {
    const button = btnEl || document.getElementById('btnRestartServices');
    button.disabled = true;
    button.textContent = 'Restarting...';
    try {
        await stopDockerContainers(button);
        setTimeout(async () => {
            await startDockerContainers(button);
        }, 2000);
    } finally {
        button.disabled = false;
        button.textContent = 'Restart Services';
    }
}

/**
 * Show extension setup instructions
 */
function showExtensionSetup() {
    const modal = `
        <div class="modal">
            <h2>Setup Browser Extension</h2>
            <ol>
                <li>Open Firefox</li>
                <li>Navigate to: about:debugging#/runtime/this-firefox</li>
                <li>Click "Load Temporary Add-on"</li>
                <li>Select manifest.json from the extension folder</li>
            </ol>
            <button onclick="window.open('about:debugging#/runtime/this-firefox', '_blank')">
                Open Firefox Debug Page
            </button>
        </div>
    `;
    // Show modal (implement your modal display logic)
    switchSection('setup');
}

// Start monitoring when page loads
document.addEventListener('DOMContentLoaded', () => {
    checkSystemStatus();
    statusCheckTimer = setInterval(checkSystemStatus, DOCKER_CHECK_INTERVAL);
    // Bind buttons if present
    document.getElementById('btnStartAllServices')?.addEventListener('click', (e) => startDockerContainers(e.currentTarget));
    document.getElementById('btnStopAllServices')?.addEventListener('click', (e) => stopDockerContainers(e.currentTarget));
    document.getElementById('btnRestartServices')?.addEventListener('click', (e) => restartDockerContainers(e.currentTarget));
});