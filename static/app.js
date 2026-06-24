import { initViewer, toggleLayer, resetCamera } from '/static/viewer.js';

// ── State ──────────────────────────────────────────────
const states = {
    upload:  document.getElementById('state-upload'),
    loading: document.getElementById('state-loading'),
    results: document.getElementById('state-results'),
};

function showState(name) {
    Object.values(states).forEach(el => el.classList.remove('active'));
    states[name].classList.add('active');
    window.scrollTo(0, 0);
}

// ── File selection ─────────────────────────────────────
const dropZone    = document.getElementById('drop-zone');
const fileInput   = document.getElementById('file-input');
const fileDisplay = document.getElementById('file-name-display');
const analyzeBtn  = document.getElementById('analyze-btn');

let selectedFile = null;
let stlBuffer    = null;

function handleFile(file) {
    if (!file || !file.name.toLowerCase().endsWith('.stl')) {
        alert('Please select a .stl file.');
        return;
    }
    if (file.size > 50 * 1024 * 1024) {
        alert('File exceeds 50 MB limit.');
        return;
    }
    selectedFile = file;
    fileDisplay.textContent = `✓ ${file.name}  (${(file.size / 1024 / 1024).toFixed(1)} MB)`;
    fileDisplay.hidden = false;
    dropZone.classList.add('has-file');
    analyzeBtn.disabled = false;

    // Read the buffer now so the 3D viewer has it after analysis completes.
    // The server deletes the file; the browser holds this copy for rendering only.
    const reader = new FileReader();
    reader.onload = (e) => { stlBuffer = e.target.result; };
    reader.readAsArrayBuffer(file);
}

fileInput.addEventListener('change', () => handleFile(fileInput.files[0]));

dropZone.addEventListener('click', (e) => {
    if (!e.target.classList.contains('file-btn')) fileInput.click();
});
dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    handleFile(e.dataTransfer.files[0]);
});

// ── Loading progress ───────────────────────────────────
const STEPS = [
    { id: 'step-geometry',  label: 'Loading geometry…',                  ms: 3000 },
    { id: 'step-draft',     label: 'Checking draft angles…',             ms: 4000 },
    { id: 'step-thickness', label: 'Measuring wall thickness…',          ms: 4000 },
    { id: 'step-undercut',  label: 'Detecting undercuts…',               ms: 4000 },
    { id: 'step-features',  label: 'Analyzing rib and corner geometry…', ms: 4000 },
    { id: 'step-interpret', label: 'Generating DFM assessment…',         ms: 99999 },
];

let progressTimer = null;
let stepIndex = 0;

function startProgress() {
    stepIndex = 0;
    STEPS.forEach(s => { document.getElementById(s.id).className = 'step'; });
    advanceStep();
}

function advanceStep() {
    if (stepIndex > 0) document.getElementById(STEPS[stepIndex - 1].id).className = 'step done';
    if (stepIndex >= STEPS.length) return;
    const step = STEPS[stepIndex];
    document.getElementById(step.id).className = 'step active';
    document.getElementById('status-msg').textContent = step.label;
    progressTimer = setTimeout(() => { stepIndex++; advanceStep(); }, step.ms);
}

function stopProgress() {
    clearTimeout(progressTimer);
    STEPS.forEach(s => { document.getElementById(s.id).className = 'step done'; });
}

// ── Analysis ───────────────────────────────────────────
analyzeBtn.addEventListener('click', async () => {
    if (!selectedFile) return;

    showState('loading');
    startProgress();

    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('pull_direction', document.getElementById('pull-direction').value);
    formData.append('prototype_method', document.getElementById('prototype-method').value);
    formData.append('production_method', document.getElementById('production-method').value);

    try {
        const resp = await fetch('/analyze', { method: 'POST', body: formData });
        const data = await resp.json();
        stopProgress();

        if (!data.success) {
            showState('upload');
            alert(`Analysis failed:\n\n${data.error}`);
            return;
        }

        renderResults(data);
        showState('results');

        // Init viewer after state is visible so canvas has layout dimensions.
        if (stlBuffer) {
            initViewer(
                document.getElementById('dfm-canvas'),
                stlBuffer,
                data.findings
            );
        }

    } catch (err) {
        stopProgress();
        showState('upload');
        alert('Could not reach the server. Make sure uvicorn is running.');
    }
});

// ── Reset ──────────────────────────────────────────────
document.getElementById('analyze-another').addEventListener('click', () => {
    selectedFile = null;
    stlBuffer    = null;
    fileDisplay.hidden = true;
    dropZone.classList.remove('has-file');
    analyzeBtn.disabled = true;
    showState('upload');
});

// ── Layer toggles ──────────────────────────────────────
document.querySelectorAll('.layer-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        btn.classList.toggle('active');
        toggleLayer(btn.dataset.check, btn.classList.contains('active'));
    });
});

document.getElementById('reset-cam').addEventListener('click', resetCamera);

// ── Render helpers ─────────────────────────────────────
const SEV_ORDER = { high: 0, medium: 1, low: 2, pass: 3, inconclusive: 4 };

const SEV_META = {
    high:         { dial: '🔴', label: 'HIGH SEVERITY',   desc: 'Significant issues require attention before proceeding.' },
    medium:       { dial: '🟡', label: 'MEDIUM SEVERITY', desc: 'Warnings to address before committing to tooling.' },
    low:          { dial: '🟡', label: 'LOW SEVERITY',    desc: 'Minor issues. Review before tooling.' },
    pass:         { dial: '✅', label: 'PASS',            desc: 'No significant DFM issues detected.' },
    inconclusive: { dial: '⚪', label: 'INCONCLUSIVE',    desc: 'Some checks could not complete. Verify mesh quality.' },
};

const CHECK_NAMES = {
    draft_angle:         'Draft Angles',
    wall_thickness:      'Wall Thickness',
    undercuts:           'Undercuts',
    rib_thickness_proxy: 'Rib Thickness',
    sharp_corners:       'Sharp Corners',
};

function sevClass(s) { return `sev-${s.toLowerCase()}`; }

function extractMeasurement(check) {
    const c = check.category;
    if (c === 'draft_angle' && check.face_count_total > 0) {
        const pct = ((check.face_count_flagged / check.face_count_total) * 100).toFixed(1);
        return `${check.face_count_flagged.toLocaleString()} of ${check.face_count_total.toLocaleString()} faces flagged (${pct}%)`;
    }
    if (c === 'wall_thickness' && check.min_measured_mm != null) {
        return `${check.min_measured_mm} mm – ${check.max_measured_mm} mm`;
    }
    if (c === 'undercuts' && check.face_count_total > 0) {
        const pct = ((check.face_count_flagged / check.face_count_total) * 100).toFixed(1);
        return `${check.face_count_flagged.toLocaleString()} of ${check.face_count_total.toLocaleString()} faces flagged (${pct}%)`;
    }
    if (c === 'rib_thickness_proxy' && check.pct_exceeding_ratio != null) {
        return `${(check.pct_exceeding_ratio * 100).toFixed(1)}% of samples exceed rib ratio`;
    }
    if (c === 'sharp_corners') {
        return `${(check.n_edges_flagged || 0).toLocaleString()} edges flagged`;
    }
    return '';
}

function buildCheckCard(check) {
    const sev  = check.severity.toLowerCase();
    const meas = extractMeasurement(check);
    return `
        <div class="check-card ${sevClass(sev)}" data-check="${check.category}">
            <div>
                <div class="check-name">${CHECK_NAMES[check.category] || check.category}</div>
                <div class="check-desc">${check.description}</div>
                ${meas ? `<div class="check-measurement">${meas}</div>` : ''}
            </div>
            <span class="sev-badge ${sevClass(sev)}">${sev}</span>
        </div>`;
}

function parseAssessment(text) {
    const sections = [];
    let current = null;
    for (const line of text.split('\n')) {
        if (line.startsWith('## ')) {
            if (current) sections.push(current);
            current = { heading: line.slice(3).trim(), lines: [] };
        } else if (current) {
            current.lines.push(line);
        }
    }
    if (current) sections.push(current);
    return sections;
}

// ── Main render ────────────────────────────────────────
function renderResults(data) {
    const f       = data.findings;
    const overall = (f.overall_effective_severity || f.overall_severity).toLowerCase();
    const meta    = SEV_META[overall] || SEV_META.medium;

    document.getElementById('results-meta').textContent =
        `${data.file_name} · ${new Date().toLocaleDateString()}`;

    const banner = document.getElementById('severity-banner');
    banner.className = `severity-banner ${sevClass(overall)}`;
    banner.innerHTML = `
        <div class="sev-dial">${meta.dial}</div>
        <div>
            <div class="sev-main-label ${sevClass(overall)}">${meta.label}</div>
            <div class="sev-desc">${meta.desc}</div>
        </div>`;

    const m  = f.mesh_summary;
    const bb = m.bounding_box_mm;
    document.getElementById('mesh-cards').innerHTML = [
        { v: m.face_count.toLocaleString(),           k: 'Faces' },
        { v: `${bb.x}×${bb.y}×${bb.z} mm`,           k: 'Bounding box' },
        { v: m.is_watertight ? 'Yes' : 'No',          k: 'Watertight' },
        { v: m.surface_area_mm2.toLocaleString()+' mm²', k: 'Surface area' },
    ].map(c => `<div class="mesh-card">
        <div class="mesh-value">${c.v}</div>
        <div class="mesh-key">${c.k}</div>
    </div>`).join('');

    const protoLabels = { sls: 'SLS nylon printing', fdm: 'FDM printing', resin: 'Resin (SLA) printing' };
    const prodLabels = { injection_molding: 'Injection molding', resin_casting: 'Resin casting (urethane)' };
    const pullDir     = f.checks.draft_angle.pull_direction;
    document.getElementById('mfg-context').innerHTML = `
        <p class="panel-label">Manufacturing Context</p>
        <div class="mfg-row"><strong>Prototype method</strong>${protoLabels[f.prototype_method] || f.prototype_method}</div>
        <div class="mfg-row"><strong>Production method</strong>${prodLabel}</div>
        <div class="mfg-row"><strong>Pull direction</strong>${pullDir} axis</div>`;

    const checks = Object.values(f.checks);
    checks.sort((a, b) => (SEV_ORDER[a.severity.toLowerCase()] ?? 9) - (SEV_ORDER[b.severity.toLowerCase()] ?? 9));

    const proto = checks.filter(c => c.stage_relevance === 'prototype' || c.stage_relevance === 'both');
    const prod  = checks.filter(c => c.stage_relevance === 'production_only' || c.stage_relevance === 'both');

    const protoBlock = document.getElementById('prototype-findings');
    if (proto.length > 0) {
        document.getElementById('proto-cards').innerHTML = proto.map(buildCheckCard).join('');
        protoBlock.hidden = false;
    } else {
        protoBlock.hidden = true;
    }
    document.getElementById('prod-cards').innerHTML = prod.map(buildCheckCard).join('');

    const sections = parseAssessment(data.interpretation);
    document.getElementById('assessment-body').innerHTML = sections.length
        ? sections.map(s => `
            <div class="assessment-block">
                <div class="assessment-heading">${s.heading}</div>
                <div class="assessment-text">${s.lines.join('\n').trim()}</div>
            </div>`).join('')
        : `<div class="assessment-text">${data.interpretation}</div>`;
}