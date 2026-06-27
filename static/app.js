import { initViewer, toggleLayer, resetCamera, focusOnCheck, setIsolationMode, setSeverityFilter, computePullSuggestion, setMaterialPreview, getMaterialPresets } from '/static/viewer.js';const states = {
    upload:  document.getElementById('state-upload'),
    loading: document.getElementById('state-loading'),
    results: document.getElementById('state-results'),
};

const MATERIAL_THRESHOLDS = {
    abs:           { min_wall: 1.5, max_wall: 4.0, min_draft: 1.0 },
    polypropylene: { min_wall: 0.8, max_wall: 3.5, min_draft: 1.0 },
    polycarbonate: { min_wall: 1.0, max_wall: 3.5, min_draft: 1.5 },
    nylon_pa6:     { min_wall: 0.8, max_wall: 3.0, min_draft: 0.5 },
    tpe:           { min_wall: 1.5, max_wall: 5.0, min_draft: 3.0 },
};

function updateThresholdPlaceholders() {
    const mat = document.getElementById('material')?.value || 'abs';
    const t   = MATERIAL_THRESHOLDS[mat] || MATERIAL_THRESHOLDS.abs;
    const minWall  = document.getElementById('custom-min-wall');
    const maxWall  = document.getElementById('custom-max-wall');
    const minDraft = document.getElementById('custom-min-draft');
    if (minWall)  minWall.placeholder  = `${t.min_wall} (${mat} default)`;
    if (maxWall)  maxWall.placeholder  = `${t.max_wall} (${mat} default)`;
    if (minDraft) minDraft.placeholder = `${t.min_draft}° (${mat} default)`;
}

document.getElementById('material')?.addEventListener('change', updateThresholdPlaceholders);
updateThresholdPlaceholders();

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
let isolated     = false;
let firstAnalysisData     = null;
let compareFile           = null;
let assemblyParts = [];
let assemblyFile  = null;
let firstStlBuffer = null;

const HISTORY_KEY = 'cad_auditor_history';
const MAX_HISTORY = 3;

function stripFindingsForHistory(findings) {
    const stripped = {
        overall_severity:           findings.overall_severity,
        overall_effective_severity: findings.overall_effective_severity,
        prototype_method:           findings.prototype_method,
        prototype_method_label:     findings.prototype_method_label,
        production_method:          findings.production_method,
        production_method_label:    findings.production_method_label,
        mesh_summary:               findings.mesh_summary,
        checks: {},
    };
    for (const [name, check] of Object.entries(findings.checks)) {
        stripped.checks[name] = {
            category:           check.category,
            severity:           check.severity,
            effective_severity: check.effective_severity,
            stage_relevance:    check.stage_relevance,
            description:        check.description,
            pull_direction:     check.pull_direction,
            face_count_flagged: check.face_count_flagged,
            face_count_total:   check.face_count_total,
            n_edges_flagged:    check.n_edges_flagged,
            n_edges_analyzed:   check.n_edges_analyzed,
            pct_too_thin:       check.pct_too_thin,
            pct_too_thick:      check.pct_too_thick,
            pct_exceeding_ratio: check.pct_exceeding_ratio,
            threshold_min_mm:   check.threshold_min_mm,
            threshold_max_mm:   check.threshold_max_mm,
            thin_face_count:    check.thin_face_indices?.length ?? 0,
            thick_face_count:   check.thick_face_indices?.length ?? 0,
            rib_flagged_count:  check.rib_flagged_face_indices?.length ?? 0,
            n_bosses_detected:  check.n_bosses_detected   ?? 0,
            worst_wall_ratio:   check.worst_wall_ratio     ?? null,
        };
    }
    return stripped;
}

function saveToHistory(data) {
    let history = getHistory();
    history.unshift({
        file_name:     data.file_name,
        material_name: data.material_name,
        material:      data.material,
        timestamp:     new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        findings:      stripFindingsForHistory(data.findings),
    });
    history = history.slice(0, MAX_HISTORY);
    try { sessionStorage.setItem(HISTORY_KEY, JSON.stringify(history)); } catch (_) {}
}

function getHistory() {
    try { return JSON.parse(sessionStorage.getItem(HISTORY_KEY) || '[]'); } catch { return []; }
}

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
    fileDisplay.textContent = `✓ ${file.name}  (${file.size < 1024 * 1024 ? (file.size / 1024).toFixed(1) + ' KB' : (file.size / 1024 / 1024).toFixed(1) + ' MB'})`;
    fileDisplay.hidden = false;
    dropZone.classList.add('has-file');
    analyzeBtn.disabled = false;

    // Read the buffer now so the 3D viewer has it after analysis completes.
    // The server deletes the file; the browser holds this copy for rendering only.
    const reader = new FileReader();
    reader.onload = (e) => {
        stlBuffer = e.target.result;

        // Suggest pull direction from geometry
        try {
            const suggestion = computePullSuggestion(stlBuffer);
            const select     = document.getElementById('pull-direction');
            const hint       = document.getElementById('pull-hint');
            if (select && hint) {
                select.value    = suggestion.suggested;
                hint.textContent = `Geometry suggests ${suggestion.suggested} axis `
                    + `(${suggestion.confidence}% of surface area). Override if your part differs.`;
                hint.hidden = false;
            }
        } catch (_) {
            // Suggestion is best-effort; silent fail is acceptable
        }
    };
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
    formData.append('material', document.getElementById('material').value);
    
    const minWallVal  = document.getElementById('custom-min-wall')?.value;
    const maxWallVal  = document.getElementById('custom-max-wall')?.value;
    const minDraftVal = document.getElementById('custom-min-draft')?.value;
    if (minWallVal)  formData.append('custom_min_wall',  minWallVal);
    if (maxWallVal)  formData.append('custom_max_wall',  maxWallVal);
    if (minDraftVal) formData.append('custom_min_draft', minDraftVal);

    try {
        const resp = await fetch('/analyze', { method: 'POST', body: formData });
        const data = await resp.json();
        stopProgress();

        if (!data.success) {
            showState('upload');
            alert(`Analysis failed:\n\n${data.error}`);
            return;
        }
        
        isolated = false;
        const isolateBtn = document.getElementById('isolate-btn');
        if (isolateBtn) isolateBtn.textContent = 'Isolate flagged';

        const slider = document.getElementById('severity-slider');
        if (slider) { slider.value = 100; document.getElementById('filter-pct').textContent = 'All'; }

        firstAnalysisData = data;
        firstStlBuffer    = stlBuffer;
        saveToHistory(data);
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
        console.error('Caught error:', err);
        alert('Error: ' + err.message);
    }
});

// ── Reset ──────────────────────────────────────────────
document.getElementById('analyze-another').addEventListener('click', () => {
    selectedFile = null;
    stlBuffer    = null;
    fileDisplay.hidden = true;
    dropZone.classList.remove('has-file');
    analyzeBtn.disabled = true;
    firstStlBuffer = null;
    showState('upload');

    firstAnalysisData  = null;
    compareFile        = null;
    assemblyParts = [];
    assemblyFile  = null;
    document.getElementById('add-to-assembly').hidden   = true;
    document.getElementById('assembly-section').hidden  = true;
    document.getElementById('assembly-file-name').hidden = true;
    document.getElementById('assembly-analyze-btn').disabled = true;
    document.getElementById('download-report').hidden = true;
    document.getElementById('compare-body').hidden   = true;
    document.getElementById('compare-toggle').textContent = 'Upload revised STL';
    document.getElementById('compare-file-name').hidden   = true;
    document.getElementById('compare-analyze-btn').disabled = true;
    document.getElementById('compare-results').hidden = true;

    document.getElementById('pull-hint').hidden = true;
    document.getElementById('custom-min-wall').value  = '';
    document.getElementById('custom-max-wall').value  = '';
    document.getElementById('custom-min-draft').value = '';
    updateThresholdPlaceholders();
    document.getElementById('preview-wrap').hidden = true;
    previewActive = false;
});

document.getElementById('reset-cam').addEventListener('click', resetCamera);

document.getElementById('add-to-assembly').addEventListener('click', () => {
    const section = document.getElementById('assembly-section');
    section.hidden = !section.hidden;
    if (!section.hidden) {
        section.scrollIntoView({ behavior: 'smooth', block: 'start' });
        const f = firstAnalysisData?.findings;
        if (f) {
            document.getElementById('assembly-settings').innerHTML = `
                <p class="compare-settings-note">
                    Each part will use the same settings:
                    <strong>${f.production_method_label || 'Injection molding'}</strong> ·
                    <strong>${firstAnalysisData.material_name || 'ABS'}</strong> ·
                    pull direction from geometry
                </p>`;
        }
        renderAssemblyPanel();
    }
});

const assemblyDropZone    = document.getElementById('assembly-drop-zone');
const assemblyFileInput   = document.getElementById('assembly-file-input');
const assemblyFileNameEl  = document.getElementById('assembly-file-name');
const assemblyAnalyzeBtn  = document.getElementById('assembly-analyze-btn');

function handleAssemblyFile(file) {
    if (!file || !file.name.toLowerCase().endsWith('.stl')) {
        alert('Please select a .stl file.');
        return;
    }
    assemblyFile = file;
    assemblyFileNameEl.textContent = `✓ ${file.name}  (${file.size < 1024 * 1024 ? (file.size / 1024).toFixed(1) + ' KB' : (file.size / 1024 / 1024).toFixed(1) + ' MB'})`;
    assemblyFileNameEl.hidden = false;
    assemblyAnalyzeBtn.disabled = false;
}

assemblyFileInput.addEventListener('change', () => handleAssemblyFile(assemblyFileInput.files[0]));
assemblyDropZone.addEventListener('click', (e) => {
    if (!e.target.classList.contains('file-btn')) assemblyFileInput.click();
});
assemblyDropZone.addEventListener('dragover', (e) => { e.preventDefault(); });
assemblyDropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    handleAssemblyFile(e.dataTransfer.files[0]);
});

assemblyAnalyzeBtn.addEventListener('click', async () => {
    if (!assemblyFile || !firstAnalysisData) return;

    document.getElementById('assembly-loading').hidden = false;
    assemblyAnalyzeBtn.disabled = true;

    const partBuffer = await assemblyFile.arrayBuffer();
    const suggestion = computePullSuggestion(partBuffer);

    const f = firstAnalysisData.findings;
    const formData = new FormData();
    formData.append('file',             assemblyFile);
    formData.append('pull_direction',   suggestion.suggested || 'Z');
    formData.append('prototype_method', f.prototype_method || 'sls');
    formData.append('production_method', f.production_method || 'injection_molding');
    formData.append('material',         firstAnalysisData.material || 'abs');

    try {
        const resp = await fetch('/analyze', { method: 'POST', body: formData });
        const data = await resp.json();

        document.getElementById('assembly-loading').hidden = true;
        assemblyAnalyzeBtn.disabled = false;

        if (!data.success) {
            alert(`Part analysis failed:\n\n${data.error}`);
            return;
        }

        assemblyParts.push({
            file_name:     data.file_name,
            material_name: data.material_name,
            findings:      data.findings,
            stlBuffer:     partBuffer,
        });
        renderAssemblyPanel();

        assemblyFile = null;
        assemblyFileNameEl.hidden = true;
        assemblyAnalyzeBtn.disabled = true;

    } catch (err) {
        document.getElementById('assembly-loading').hidden = true;
        assemblyAnalyzeBtn.disabled = false;
        alert('Could not reach the server.');
    }
});

document.getElementById('assembly-check-btn')?.addEventListener('click', runAssemblyChecks);

let previewActive = false;

document.getElementById('preview-toggle').addEventListener('click', () => {
    previewActive = !previewActive;
    const btn    = document.getElementById('preview-toggle');
    const sel    = document.getElementById('preview-select');
    const note   = document.getElementById('material-note');
    const hint   = document.getElementById('viewport-hint');

    if (previewActive) {
        btn.textContent = 'Back to DFM view';
        btn.classList.add('preview-active');
        sel.hidden = false;
        setMaterialPreview(sel.value, true);
        const presets = getMaterialPresets();
        note.textContent = presets[sel.value]?.note || '';
        note.hidden = false;
        hint.hidden = true;
    } else {
        btn.textContent = 'Material preview';
        btn.classList.remove('preview-active');
        sel.hidden = true;
        setMaterialPreview(null, false);
        note.hidden = true;
        hint.hidden = false;
    }
});

document.getElementById('preview-select').addEventListener('change', (e) => {
    if (!previewActive) return;
    setMaterialPreview(e.target.value, true);
    const presets = getMaterialPresets();
    const note = document.getElementById('material-note');
    note.textContent = presets[e.target.value]?.note || '';
});

document.getElementById('severity-slider').addEventListener('input', (e) => {
    const val = parseInt(e.target.value);
    const pctEl = document.getElementById('filter-pct');
    pctEl.textContent = val === 100 ? 'All' : val + '%';
    setSeverityFilter(val / 100);
});

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
    boss_detection:      'Boss Detection',
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
    if (c === 'boss_detection') {
        return check.n_bosses_detected > 0
            ? `${check.n_bosses_detected} boss candidate${check.n_bosses_detected > 1 ? 's' : ''} · worst ratio ${check.worst_wall_ratio ? Math.round(check.worst_wall_ratio * 100) + '%' : 'N/A'} of nominal`
            : '';
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

    const protoLabels = { sls: 'SLS nylon printing', fdm: 'FDM printing', resin: 'Resin (SLA) printing', resin_casting: 'Resin casting (urethane)' };
    const prodLabels = { injection_molding: 'Injection molding', resin_casting: 'Resin casting (urethane)' };
    const pullDir     = f.checks.draft_angle.pull_direction;
    const matName  = data.material_name || 'ABS';
    const prodLabel = f.production_method_label || 'Injection molding';
    document.getElementById('mfg-context').innerHTML = `
        <p class="panel-label">Manufacturing Context</p>
        <div class="mfg-row"><strong>Material</strong>${matName}</div>
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
    
    // Wire check cards for viewport focus and face count badges
    setTimeout(() => {
        document.querySelectorAll('.check-card').forEach(card => {
            const checkName = card.dataset.check;
            const viewerMap = {
                draft_angle:         'draft_angle',
                undercuts:           'undercuts',
                wall_thickness:      'wall_thin',
                rib_thickness_proxy: 'rib_thickness',
                sharp_corners:       'sharp_corners',
            };
            if (viewerMap[checkName]) {
                card.style.cursor = 'pointer';
                card.title = 'Click to focus viewport on these faces';
                card.addEventListener('click', () => {
                    focusOnCheck(viewerMap[checkName]);
                    document.getElementById('dfm-canvas')
                        .scrollIntoView({ behavior: 'smooth', block: 'center' });
                });
            }
        });
    }, 0);

    const sections = parseAssessment(data.interpretation);
    document.getElementById('assessment-body').innerHTML = sections.length
        ? sections.map(s => `
            <div class="assessment-block">
                <div class="assessment-heading">${s.heading}</div>
                <div class="assessment-text">${s.lines.join('\n').trim()}</div>
            </div>`).join('')
        : `<div class="assessment-text">${data.interpretation}</div>`;

    assemblyParts = [];
    document.getElementById('assembly-section').hidden = true;
    document.getElementById('add-to-assembly').hidden  = false;
    document.getElementById('preview-wrap').hidden = false;
    document.getElementById('download-report').hidden = false;
    previewActive = false;
    const previewBtn = document.getElementById('preview-toggle');
    if (previewBtn) {
        previewBtn.textContent = 'Material preview';
        previewBtn.classList.remove('preview-active');
    }
    const previewSel = document.getElementById('preview-select');
    if (previewSel) previewSel.hidden = true;
    const materialNote = document.getElementById('material-note');
    if (materialNote) materialNote.hidden = true;
    const vpHint = document.getElementById('viewport-hint');
    if (vpHint) vpHint.hidden = false;
}

// ── PDF / HTML report download ─────────────────────────────────

document.getElementById('download-report').addEventListener('click', () => {
    if (!firstAnalysisData) return;
    const html = buildReportHTML(firstAnalysisData);
    const blob = new Blob([html], { type: 'text/html' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = `cad-auditor-${(firstAnalysisData.file_name || 'report').replace('.stl', '')}.html`;
    a.click();
    URL.revokeObjectURL(url);
});

function buildReportHTML(data) {
    const f       = data.findings;
    const overall = (f.overall_effective_severity || f.overall_severity || 'pass').toLowerCase();
    const date    = new Date().toLocaleDateString();
    const m       = f.mesh_summary;
    const bb      = m.bounding_box_mm;

    const sevColor = {
        high: '#dc2626', medium: '#d97706', low: '#ca8a04',
        pass: '#16a34a', inconclusive: '#64748b',
    }[overall] || '#64748b';

    const sevBg = {
        high: '#fef2f2', medium: '#fffbeb', low: '#fefce8',
        pass: '#f0fdf4', inconclusive: '#f8fafc',
    }[overall] || '#f8fafc';

    const checkNames = {
        draft_angle:         'Draft Angles',
        wall_thickness:      'Wall Thickness',
        undercuts:           'Undercuts',
        rib_thickness_proxy: 'Rib Thickness',
        sharp_corners:       'Sharp Corners',
        boss_detection:      'Boss Detection',
    };

    const SEV_ORDER_RPT = { high: 0, medium: 1, low: 2, pass: 3, inconclusive: 4 };

    const checks = Object.values(f.checks).sort((a, b) => {
        const as = (a.effective_severity || a.severity || 'pass').toLowerCase();
        const bs = (b.effective_severity || b.severity || 'pass').toLowerCase();
        return (SEV_ORDER_RPT[as] ?? 4) - (SEV_ORDER_RPT[bs] ?? 4);
    });

    function sevStyle(s) {
        const c = {
            high: '#dc2626', medium: '#d97706', low: '#ca8a04',
            pass: '#16a34a', inconclusive: '#64748b',
        }[s] || '#64748b';
        const bg = {
            high: '#fef2f2', medium: '#fffbeb', low: '#fefce8',
            pass: '#f0fdf4', inconclusive: '#f8fafc',
        }[s] || '#f8fafc';
        return `background:${bg}; color:${c}; padding:3px 8px; border-radius:4px; font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.05em;`;
    }

    function checkRows() {
        return checks.map(c => {
            const sev = (c.effective_severity || c.severity || 'pass').toLowerCase();
            return `
            <tr>
                <td style="padding:10px 12px; font-weight:600; font-size:13px; color:#0f172a">${checkNames[c.category] || c.category}</td>
                <td style="padding:10px 12px"><span style="${sevStyle(sev)}">${sev}</span></td>
                <td style="padding:10px 12px; font-size:13px; color:#64748b; line-height:1.5">${c.description || ''}</td>
                <td style="padding:10px 12px; font-size:11px; font-family:monospace; color:#64748b">${c.stage_relevance === 'production_only' ? 'Pre-tooling' : 'Pre-prototype'}</td>
            </tr>`;
        }).join('');
    }

    function formatAssessment(text) {
        if (!text) return '<p style="color:#64748b">No interpretation available.</p>';
        return text
            .split('\n')
            .map(line => {
                if (line.startsWith('## '))
                    return `<h3 style="font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:0.07em; color:#64748b; margin:20px 0 8px; padding-bottom:6px; border-bottom:1px solid #e2e8f0">${line.slice(3)}</h3>`;
                if (line.trim())
                    return `<p style="font-size:14px; color:#0f172a; line-height:1.75; margin:0 0 8px">${line}</p>`;
                return '';
            }).join('');
    }

    const protoLabels = { sls: 'SLS nylon printing', fdm: 'FDM printing', resin: 'Resin (SLA)', resin_casting: 'Resin casting (urethane)' };

    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>CAD Auditor — ${data.file_name}</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f1f5f9; color: #0f172a; }
  .page { max-width: 860px; margin: 0 auto; padding: 40px 24px; }
  @media print {
    body { background: white; }
    .page { padding: 20px; }
    .no-print { display: none; }
  }
  table { width: 100%; border-collapse: collapse; }
  th { background: #f8fafc; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: #64748b; padding: 8px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; }
  tr:nth-child(even) td { background: #f8fafc; }
  td { border-bottom: 1px solid #e2e8f0; vertical-align: top; }
</style>
</head>
<body>
<div class="page">

  <div style="background:#0f172a; border-radius:12px; padding:24px 28px; margin-bottom:24px; display:flex; align-items:center; justify-content:space-between">
    <div>
      <div style="font-size:20px; font-weight:700; color:#f8fafc">⬡ CAD Auditor</div>
      <div style="font-size:13px; color:#94a3b8; margin-top:4px">DFM Review Report</div>
    </div>
    <div style="text-align:right">
      <div style="font-size:14px; font-weight:500; color:#f8fafc">${data.file_name}</div>
      <div style="font-size:12px; color:#94a3b8; margin-top:2px">${date}</div>
    </div>
  </div>

  <div style="background:${sevBg}; border:1.5px solid ${sevColor}33; border-radius:10px; padding:18px 22px; margin-bottom:20px; display:flex; align-items:center; gap:14px">
    <div style="font-size:32px">${overall === 'pass' ? '✅' : overall === 'medium' ? '🟡' : overall === 'high' ? '🔴' : '⚪'}</div>
    <div>
      <div style="font-size:20px; font-weight:700; color:${sevColor}">${overall.toUpperCase()} SEVERITY</div>
      <div style="font-size:13px; color:#64748b; margin-top:2px">Overall DFM assessment for ${data.file_name}</div>
    </div>
  </div>

  <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:20px">
    <div style="background:white; border:1px solid #e2e8f0; border-radius:8px; padding:16px 18px">
      <div style="font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.07em; color:#64748b; margin-bottom:10px">Part Summary</div>
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px">
        <div><div style="font-family:monospace; font-size:15px; font-weight:500">${m.face_count.toLocaleString()}</div><div style="font-size:11px; color:#64748b; text-transform:uppercase; letter-spacing:0.05em">Faces</div></div>
        <div><div style="font-family:monospace; font-size:13px; font-weight:500">${bb.x}×${bb.y}×${bb.z}</div><div style="font-size:11px; color:#64748b; text-transform:uppercase; letter-spacing:0.05em">Bounding box (mm)</div></div>
        <div><div style="font-family:monospace; font-size:15px; font-weight:500">${m.is_watertight ? 'Yes' : 'No'}</div><div style="font-size:11px; color:#64748b; text-transform:uppercase; letter-spacing:0.05em">Watertight</div></div>
        <div><div style="font-family:monospace; font-size:13px; font-weight:500">${m.surface_area_mm2.toLocaleString()} mm²</div><div style="font-size:11px; color:#64748b; text-transform:uppercase; letter-spacing:0.05em">Surface area</div></div>
      </div>
    </div>
    <div style="background:white; border:1px solid #e2e8f0; border-radius:8px; padding:16px 18px">
      <div style="font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.07em; color:#64748b; margin-bottom:10px">Manufacturing Context</div>
      ${[
        ['Material', data.material_name || 'ABS'],
        ['Prototype method', protoLabels[f.prototype_method] || f.prototype_method || '—'],
        ['Production method', f.production_method_label || 'Injection molding'],
        ['Pull direction', `${f.checks.draft_angle?.pull_direction || 'Z'} axis`],
      ].map(([k, v]) => `<div style="display:flex; justify-content:space-between; padding:3px 0; font-size:13px"><span style="color:#64748b">${k}</span><span style="font-weight:500">${v}</span></div>`).join('')}
    </div>
  </div>

  <div style="background:white; border:1px solid #e2e8f0; border-radius:8px; margin-bottom:20px; overflow:hidden">
    <div style="padding:14px 18px; background:#f8fafc; border-bottom:1px solid #e2e8f0">
      <div style="font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.07em; color:#64748b">Check Results</div>
    </div>
    <table>
      <thead><tr><th style="width:140px">Check</th><th style="width:90px">Severity</th><th>Finding</th><th style="width:100px">When to fix</th></tr></thead>
      <tbody>${checkRows()}</tbody>
    </table>
  </div>

  <div style="background:white; border:1px solid #e2e8f0; border-radius:8px; padding:22px 24px; margin-bottom:20px">
    <div style="font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.07em; color:#64748b; margin-bottom:16px">DFM Assessment</div>
    ${formatAssessment(data.interpretation)}
  </div>

  <div style="font-size:11px; color:#94a3b8; text-align:center; padding:16px 0; border-top:1px solid #e2e8f0">
    Geometric analysis is deterministic. Interpretation generated by Claude. Your STL file was deleted after processing. Only measurement data reached the AI provider.
  </div>

  <div class="no-print" style="text-align:center; margin-top:20px">
    <button onclick="window.print()" style="background:#2563eb; color:white; border:none; border-radius:8px; padding:10px 24px; font-size:14px; cursor:pointer">Print / Save as PDF</button>
  </div>

</div>
</body>
</html>`;
}

// ── Version comparison ─────────────────────────────────────────


const CHECK_DISPLAY = {
    draft_angle:         'Draft Angles',
    wall_thickness:      'Wall Thickness',
    undercuts:           'Undercuts',
    rib_thickness_proxy: 'Rib Thickness',
    sharp_corners:       'Sharp Corners',
    boss_detection:      'Boss Detection',
};

document.getElementById('compare-toggle').addEventListener('click', () => {
    const body = document.getElementById('compare-body');
    const btn  = document.getElementById('compare-toggle');
    body.hidden = !body.hidden;
    btn.textContent = body.hidden ? 'Upload revised STL' : 'Hide';

    if (!body.hidden && firstAnalysisData) {
        const f = firstAnalysisData.findings;
        document.getElementById('compare-settings').innerHTML = `
            <p class="compare-settings-note">
                Revision will use the same settings:
                <strong>${f.production_method_label || 'Injection molding'}</strong> ·
                <strong>${firstAnalysisData.material_name || 'ABS'}</strong> ·
                <strong>${f.checks.draft_angle?.pull_direction || 'Z'} axis</strong>
            </p>`;

        const history  = getHistory();
        const baseline = document.getElementById('compare-baseline');
        const baseWrap = document.getElementById('compare-baseline-wrap');

        if (history.length > 1 && baseline) {
            baseline.innerHTML = history.map((h, i) =>
                `<option value="${i}">${i === 0 ? 'Current' : `V${history.length - i}`} — ${h.file_name} (${h.timestamp})</option>`
            ).join('');
            baseline.value = '1';
            baseWrap.hidden = false;
        } else {
            if (baseWrap) baseWrap.hidden = true;
        }
    }
});

const compareDropZone  = document.getElementById('compare-drop-zone');
const compareFileInput = document.getElementById('compare-file-input');
const compareFileName  = document.getElementById('compare-file-name');
const compareAnalyzeBtn = document.getElementById('compare-analyze-btn');

function handleCompareFile(file) {
    if (!file || !file.name.toLowerCase().endsWith('.stl')) {
        alert('Please select a .stl file.');
        return;
    }
    compareFile = file;
    compareFileName.textContent = `✓ ${file.name}  (${(file.size / 1024 / 1024).toFixed(1)} MB)`;
    compareFileName.hidden = false;
    compareAnalyzeBtn.disabled = false;

    const reader = new FileReader();
    reader.readAsArrayBuffer(file);
}

compareFileInput.addEventListener('change', () => handleCompareFile(compareFileInput.files[0]));

compareDropZone.addEventListener('click', (e) => {
    if (!e.target.classList.contains('file-btn')) compareFileInput.click();
});
compareDropZone.addEventListener('dragover', (e) => { e.preventDefault(); });
compareDropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    handleCompareFile(e.dataTransfer.files[0]);
});

compareAnalyzeBtn.addEventListener('click', async () => {
    if (!compareFile || !firstAnalysisData) return;

    document.getElementById('compare-loading').hidden  = false;
    document.getElementById('compare-results').hidden  = true;
    compareAnalyzeBtn.disabled = true;

    const history     = getHistory();
    const baselineIdx = parseInt(document.getElementById('compare-baseline')?.value ?? '0', 10);
    const baseline    = history[baselineIdx] ?? firstAnalysisData;
    const baseFindings = baseline?.findings ?? firstAnalysisData?.findings;

    const f = baseFindings;
    const formData = new FormData();
    formData.append('file',             compareFile);
    formData.append('pull_direction',   f.checks.draft_angle?.pull_direction || 'Z');
    formData.append('prototype_method', f.prototype_method || 'sls');
    formData.append('production_method', f.production_method || 'injection_molding');
    formData.append('material',         firstAnalysisData.material || 'abs');

    try {
        const resp = await fetch('/analyze', { method: 'POST', body: formData });
        const data = await resp.json();

        document.getElementById('compare-loading').hidden = true;
        compareAnalyzeBtn.disabled = false;

        if (!data.success) {
            alert(`Revision analysis failed:\n\n${data.error}`);
            return;
        }

        renderComparison(baseFindings, data.findings, compareFile.name);
        document.getElementById('compare-results').hidden = false;

    } catch (err) {
        document.getElementById('compare-loading').hidden = true;
        compareAnalyzeBtn.disabled = false;
        alert('Could not reach the server.');
    }
});

function getPct(check) {
    const name = check.category;
    if (name === 'draft_angle' || name === 'undercuts') {
        const total = check.face_count_total;
        if (total > 0) return check.face_count_flagged / total * 100;
    } else if (name === 'sharp_corners') {
        if (check.n_edges_analyzed > 0)
            return check.n_edges_flagged / check.n_edges_analyzed * 100;
    } else if (name === 'wall_thickness') {
        return ((check.pct_too_thin || 0) + (check.pct_too_thick || 0)) * 100;
    } else if (name === 'rib_thickness_proxy') {
        return (check.pct_exceeding_ratio || 0) * 100;
    }
    return null;
}

function generateWhatToFixNext(after, cmpResult) {
    const improved  = Object.entries(cmpResult.checks).filter(([_, c]) => c.status === 'improved' || c.status === 'resolved');
    const worse     = Object.entries(cmpResult.checks).filter(([_, c]) => c.status === 'worse'    || c.status === 'new_issue');

    const remaining = Object.entries(after.checks)
        .filter(([_, c]) => {
            const s = (c.effective_severity || c.severity || 'pass').toLowerCase();
            return s !== 'pass' && s !== 'inconclusive';
        })
        .sort((a, b) =>
            (SEV_ORDER[(a[1].effective_severity || a[1].severity || 'pass').toLowerCase()] ?? 4) -
            (SEV_ORDER[(b[1].effective_severity || b[1].severity || 'pass').toLowerCase()] ?? 4)
        );

    let msg = cmpResult.overall.trend === 'improved' ? 'This revision is an improvement. '
            : cmpResult.overall.trend === 'worse'    ? 'This revision introduced new issues. '
            :                                          'Overall severity is unchanged. ';

    if (improved.length > 0)
        msg += `${improved.map(([k]) => CHECK_DISPLAY[k] || k).join(' and ')} improved. `;

    if (worse.length > 0)
        msg += `Watch out: ${worse.map(([k]) => CHECK_DISPLAY[k] || k).join(' and ')} got worse. `;

    if (remaining.length > 0) {
        const [topName, topCheck] = remaining[0];
        const sev = (topCheck.effective_severity || topCheck.severity || '').toLowerCase();
        msg += `Your highest remaining issue is ${CHECK_DISPLAY[topName] || topName} at ${sev.toUpperCase()} severity — address this before committing to production tooling.`;
    } else {
        msg += 'All checks are now passing. The part is ready for the next stage.';
    }

    return msg;
}

function compareFindings(before, after) {
    const result = {
        overall: {
            before: (before.overall_effective_severity || before.overall_severity || 'pass').toLowerCase(),
            after:  (after.overall_effective_severity  || after.overall_severity  || 'pass').toLowerCase(),
        },
        checks: {},
        improved: 0,
        worse:    0,
        resolved: 0,
        new_issue: 0,
        unchanged: 0,
    };

    for (const checkName of Object.keys(before.checks)) {
        const b = before.checks[checkName];
        const a = after.checks[checkName];
        if (!b || !a) continue;

        const bSev = (b.effective_severity || b.severity || 'pass').toLowerCase();
        const aSev = (a.effective_severity || a.severity || 'pass').toLowerCase();
        const bOrd = SEV_ORDER[bSev] ?? 4;
        const aOrd = SEV_ORDER[aSev] ?? 4;

        let status;
        if      (bSev !== 'pass' && aSev === 'pass') { status = 'resolved';  result.resolved++;  }
        else if (bSev === 'pass' && aSev !== 'pass') { status = 'new_issue'; result.new_issue++; }
        else if (aOrd > bOrd)                        { status = 'improved';  result.improved++;  }
        else if (aOrd < bOrd)                        { status = 'worse';     result.worse++;     }
        else                                         { status = 'unchanged'; result.unchanged++; }

        let bCount = null, aCount = null;
        if (checkName === 'draft_angle' || checkName === 'undercuts') {
            bCount = b.face_count_flagged ?? null;
            aCount = a.face_count_flagged ?? null;
        } else if (checkName === 'sharp_corners') {
            bCount = b.n_edges_flagged ?? null;
            aCount = a.n_edges_flagged ?? null;
        } else if (checkName === 'wall_thickness') {
            bCount = (b.thin_face_indices?.length ?? b.thin_face_count ?? 0)
                   + (b.thick_face_indices?.length ?? b.thick_face_count ?? 0);
            aCount = (a.thin_face_indices?.length ?? a.thin_face_count ?? 0)
                   + (a.thick_face_indices?.length ?? a.thick_face_count ?? 0);
        } else if (checkName === 'rib_thickness_proxy') {
            bCount = b.rib_flagged_face_indices?.length ?? b.rib_flagged_count ?? null;
            aCount = a.rib_flagged_face_indices?.length ?? a.rib_flagged_count ?? null;
        } else if (checkName === 'boss_detection') {
            bCount = b.n_bosses_detected ?? null;
            aCount = a.n_bosses_detected ?? null;
        }

        const bPct = getPct(b);
        const aPct = getPct(a);

        result.checks[checkName] = {
            bSev, aSev, status,
            bCount, aCount,
            delta: (bCount !== null && aCount !== null) ? aCount - bCount : null,
            bPct, aPct,
        };
    }

    const bOvd = SEV_ORDER[result.overall.before] ?? 4;
    const aOvd = SEV_ORDER[result.overall.after]  ?? 4;
    result.overall.trend = aOvd > bOvd ? 'improved' : aOvd < bOvd ? 'worse' : 'unchanged';

    return result;
}

function renderComparison(before, after, newFileName) {
    const cmp = compareFindings(before, after);

    const overallEmoji = { improved: '✅', worse: '🔴', unchanged: '➖' };
    const overallLabel = { improved: 'Overall severity improved', worse: 'Overall severity got worse', unchanged: 'Overall severity unchanged' };

    const statusMeta = {
        resolved:  { label: 'Resolved',   cls: 'cmp-resolved'  },
        improved:  { label: 'Improved',   cls: 'cmp-improved'  },
        new_issue: { label: 'New issue',  cls: 'cmp-worse'     },
        worse:     { label: 'Got worse',  cls: 'cmp-worse'     },
        unchanged: { label: 'Unchanged',  cls: 'cmp-unchanged' },
    };

    function sevBadge(s) {
        return `<span class="sev-badge sev-${s}">${s}</span>`;
    }

    function deltaStr(c) {
        const d    = c.delta;
        const bPct = c.bPct !== null && c.bPct !== undefined ? c.bPct.toFixed(1) + '%' : null;
        const aPct = c.aPct !== null && c.aPct !== undefined ? c.aPct.toFixed(1) + '%' : null;
        const pctStr = (bPct && aPct) ? ` (${bPct} → ${aPct})` : '';

        if (d === null)  return '';
        if (d === 0)     return `<span class="cmp-delta-zero">±0${pctStr}</span>`;
        if (d < 0)       return `<span class="cmp-delta-better">▼ ${Math.abs(d).toLocaleString()}${pctStr}</span>`;
        return               `<span class="cmp-delta-worse">▲ ${d.toLocaleString()}${pctStr}</span>`;
    }

    const summaryParts = [];
    if (cmp.resolved  > 0) summaryParts.push(`<span class="cmp-resolved">${cmp.resolved} resolved</span>`);
    if (cmp.improved  > 0) summaryParts.push(`<span class="cmp-improved">${cmp.improved} improved</span>`);
    if (cmp.unchanged > 0) summaryParts.push(`<span class="cmp-unchanged">${cmp.unchanged} unchanged</span>`);
    if (cmp.worse     > 0) summaryParts.push(`<span class="cmp-worse">${cmp.worse} got worse</span>`);
    if (cmp.new_issue > 0) summaryParts.push(`<span class="cmp-worse">${cmp.new_issue} new issue</span>`);

    let rows = '';
    for (const [checkName, c] of Object.entries(cmp.checks)) {
        const sm = statusMeta[c.status];
        rows += `
        <div class="cmp-row">
            <span class="cmp-check-name">${CHECK_DISPLAY[checkName] || checkName}</span>
            <div class="cmp-sev-change">
                ${sevBadge(c.bSev)}
                <span class="cmp-arrow">→</span>
                ${sevBadge(c.aSev)}
            </div>
            ${c.delta !== null ? `<span class="cmp-delta">${deltaStr(c)}</span>` : '<span></span>'}
            <span class="cmp-status-tag ${sm.cls}">${sm.label}</span>
        </div>`;
    }

    const nextSteps = generateWhatToFixNext(after, cmp);

    document.getElementById('compare-results').innerHTML = `
        <div class="cmp-overall">
            <span class="cmp-overall-icon">${overallEmoji[cmp.overall.trend]}</span>
            <div>
                <div class="cmp-overall-label">${overallLabel[cmp.overall.trend]}</div>
                <div class="cmp-overall-subs">
                    ${sevBadge(cmp.overall.before)} → ${sevBadge(cmp.overall.after)}
                    &nbsp;·&nbsp; comparing <strong>${newFileName}</strong> against original
                </div>
            </div>
        </div>
        <div class="cmp-summary">${summaryParts.join(' · ')}</div>
        <div class="cmp-rows">${rows}</div>
        <div class="cmp-next-steps">
            <span class="cmp-next-label">What to fix next</span>
            ${nextSteps}
        </div>`;
}

// ── Assembly panel ──────────────────────────────────────────────


function getSev(findings) {
    return (findings.overall_effective_severity || findings.overall_severity || 'pass').toLowerCase();
}

function getTopIssue(findings) {
    const checks = Object.values(findings.checks);
    checks.sort((a, b) => {
        const as = (a.effective_severity || a.severity || 'pass').toLowerCase();
        const bs = (b.effective_severity || b.severity || 'pass').toLowerCase();
        return (SEV_ORDER[as] ?? 4) - (SEV_ORDER[bs] ?? 4);
    });
    const top = checks[0];
    if (!top) return null;
    const sev = (top.effective_severity || top.severity || 'pass').toLowerCase();
    if (sev === 'pass') return null;
    return { name: CHECK_DISPLAY[top.category] || top.category, severity: sev };
}

function renderAssemblyPanel() {
    if (!firstAnalysisData) return;

    const allParts = [
        { file_name: firstAnalysisData.file_name, material_name: firstAnalysisData.material_name, findings: firstAnalysisData.findings },
        ...assemblyParts,
    ];

    document.getElementById('assembly-count').textContent = `${allParts.length} part${allParts.length > 1 ? 's' : ''}`;

    // Critical path
    const criticalPart = allParts.reduce((worst, p) => {
        return (SEV_ORDER[getSev(p.findings)] ?? 4) <= (SEV_ORDER[getSev(worst.findings)] ?? 4) ? p : worst;
    });
    const critSev = getSev(criticalPart.findings);
    const critTop = getTopIssue(criticalPart.findings);

    document.getElementById('assembly-critical').innerHTML = critSev === 'pass'
        ? `<div class="assembly-critical-pass">✅ All parts pass DFM review. Assembly is ready for production tooling.</div>`
        : `<div class="assembly-critical-issue">
            <div class="assembly-critical-label">Critical path</div>
            <div class="assembly-critical-body">
                <strong>${criticalPart.file_name}</strong> is the bottleneck at
                <span class="sev-badge sev-${critSev}">${critSev}</span> severity.
                ${critTop ? `Primary issue: ${critTop.name}. ` : ''}
                Resolve this part before committing any part to production tooling.
            </div>
           </div>`;

    // Pull direction consistency
    const pullDirs = allParts.map(p => ({
        name: p.file_name,
        dir:  p.findings.checks.draft_angle?.pull_direction || '?',
    }));
    const uniqueDirs = [...new Set(pullDirs.map(p => p.dir))];
    const pullConsistent = uniqueDirs.length === 1;

    document.getElementById('assembly-pull').innerHTML = `
        <div class="assembly-pull-row">
            <span class="assembly-pull-icon">${pullConsistent ? '✓' : '⚠'}</span>
            <div>
                <span class="assembly-pull-label">Pull directions:</span>
                ${pullDirs.map(p => `<span class="assembly-pull-tag">${p.name} → <strong>${p.dir}</strong></span>`).join(' ')}
                ${pullConsistent
                    ? '<span class="assembly-pull-ok">Consistent — single mold orientation for all parts.</span>'
                    : '<span class="assembly-pull-warn">Parts require different mold orientations. Each part needs separate tooling setup.</span>'
                }
            </div>
        </div>`;

    // Combined totals
    let totalDraft = 0, totalUndercut = 0, totalSharp = 0;
    for (const p of allParts) {
        totalDraft    += p.findings.checks.draft_angle?.face_count_flagged    || 0;
        totalUndercut += p.findings.checks.undercuts?.face_count_flagged       || 0;
        totalSharp    += p.findings.checks.sharp_corners?.n_edges_flagged      || 0;
    }

    // Per-part cards
    const partCards = allParts.map((p, i) => {
        const sev  = getSev(p.findings);
        const top  = getTopIssue(p.findings);
        return `
        <div class="assembly-part-card">
            <div class="assembly-part-num">Part ${i + 1}</div>
            <div class="assembly-part-name">${p.file_name}</div>
            <span class="sev-badge sev-${sev}">${sev}</span>
            <div class="assembly-part-issue">${top ? top.name : 'No issues'}</div>
            <div class="assembly-part-faces">${p.findings.mesh_summary.face_count.toLocaleString()} faces</div>
        </div>`;
    }).join('');

    document.getElementById('assembly-parts').innerHTML = `
        <div class="assembly-parts-grid">${partCards}</div>
        <div class="assembly-totals">
            Assembly totals —
            Draft: <strong>${totalDraft.toLocaleString()}</strong> faces ·
            Undercuts: <strong>${totalUndercut.toLocaleString()}</strong> faces ·
            Sharp edges: <strong>${totalSharp.toLocaleString()}</strong>
        </div>`;

    // Show assembly check button when 2+ parts available
    const checkBtn = document.getElementById('assembly-check-btn');
    if (checkBtn) {
        checkBtn.hidden  = allParts.length < 2;
        checkBtn.disabled = false;
        checkBtn.textContent = 'Run Assembly Checks';
    }
    document.getElementById('assembly-check-results').hidden = true;
}

// ── Assembly checks (interference, mating face, tolerance stack) ───

async function runAssemblyChecks() {
    if (!firstStlBuffer || assemblyParts.length === 0) return;

    const btn = document.getElementById('assembly-check-btn');
    const results = document.getElementById('assembly-check-results');
    btn.disabled = true;
    btn.textContent = 'Running checks…';
    results.hidden = true;

    const partB = assemblyParts[0];
    if (!partB.stlBuffer) {
        alert('Part B buffer not available. Re-add the part.');
        btn.disabled = false;
        btn.textContent = 'Run Assembly Checks';
        return;
    }

    const formData = new FormData();
    formData.append('file_a', new Blob([firstStlBuffer], { type: 'application/octet-stream' }),
        firstAnalysisData.file_name || 'part_a.stl');
    formData.append('file_b', new Blob([partB.stlBuffer], { type: 'application/octet-stream' }),
        partB.file_name || 'part_b.stl');
    formData.append('production_method',
        firstAnalysisData.findings.production_method || 'injection_molding');

    try {
        const resp = await fetch('/check-assembly', { method: 'POST', body: formData });
        const data = await resp.json();

        btn.disabled = false;
        btn.textContent = 'Run Assembly Checks';

        if (!data.success) {
            alert(`Assembly check failed:\n\n${data.error}`);
            return;
        }

        renderAssemblyChecks(data);
        results.hidden = false;

    } catch (err) {
        btn.disabled = false;
        btn.textContent = 'Run Assembly Checks';
        alert('Could not reach the server.');
    }
}

function renderAssemblyChecks(data) {
    const el = document.getElementById('assembly-check-results');

    // Interference
    const int = data.interference;
    const intIcon = int.has_interference === false ? '✅'
                  : int.has_interference === true  ? '🔴' : '⚪';
    const intSev  = int.severity || 'inconclusive';

    // Mating faces
    const mat = data.mating_faces;
    const matIcon = mat.verdict === 'good' ? '✅'
                  : mat.verdict === 'review' ? '🟡'
                  : mat.verdict === 'poor' ? '🔴' : '⚪';

    // Tolerance stack — pick the axis with the largest stack dimension
    const tol = data.tolerance_stack;
    const axes = ['x', 'y', 'z'];
    const stackRows = axes.map(axis => {
        const t = tol[axis];
        const flag = t.worst_case_tolerance_mm > 1.0 ? '⚠' : '✓';
        return `<tr>
            <td class="ac-td">${axis.toUpperCase()} axis</td>
            <td class="ac-td-mono">${t.stack_dimension_mm}mm</td>
            <td class="ac-td-mono">±${t.worst_case_tolerance_mm}mm</td>
            <td class="ac-td-mono">±${t.rss_tolerance_mm}mm</td>
            <td class="ac-td">${flag}</td>
        </tr>`;
    }).join('');

    el.innerHTML = `
        <div class="ac-section">
            <div class="ac-section-label">Coordinate Frame Requirement</div>
            <div class="ac-warning">
                ⚠ Interference and mating face results are only valid if both STL files
                were exported from Fusion 360 with the same world origin. Use
                File → Export and do not reposition parts between exports.
            </div>
        </div>

        <div class="ac-section">
            <div class="ac-section-label">Interference Detection</div>
            <div class="ac-result-row">
                <span class="ac-icon">${intIcon}</span>
                <div>
                    <span class="sev-badge sev-${intSev}">${intSev}</span>
                    <p class="ac-desc">${int.description}</p>
                    ${int.has_interference === true ? `
                    <div class="ac-measurements">
                        Part B inside A: <strong>${int.pct_b_inside_a}%</strong> of samples ·
                        Part A inside B: <strong>${int.pct_a_inside_b}%</strong> of samples
                    </div>` : ''}
                </div>
            </div>
        </div>

        <div class="ac-section">
            <div class="ac-section-label">Mating Face Analysis
                <span class="ac-pair-note">${data.part_a} ↔ ${data.part_b}</span>
            </div>
            <div class="ac-result-row">
                <span class="ac-icon">${matIcon}</span>
                <div>
                    <p class="ac-desc">${mat.description}</p>
                    ${mat.found ? `
                    <div class="ac-measurements">
                        Gap: <strong>${mat.gap_mm}mm</strong> ·
                        Parallelism error: <strong>${mat.parallelism_error_degrees}°</strong> ·
                        Face areas: <strong>${mat.area_a_mm2}mm²</strong> vs
                        <strong>${mat.area_b_mm2}mm²</strong>
                        (${Math.round(mat.area_ratio * 100)}% match)
                    </div>` : ''}
                </div>
            </div>
        </div>

        <div class="ac-section">
            <div class="ac-section-label">Tolerance Stack
                <span class="ac-pair-note">Injection molding: ±0.08mm + ±0.002mm/mm</span>
            </div>
            <table class="ac-tol-table">
                <thead>
                    <tr>
                        <th class="ac-th">Axis</th>
                        <th class="ac-th">Stack dimension</th>
                        <th class="ac-th">Worst case (linear)</th>
                        <th class="ac-th">Realistic (RSS)</th>
                        <th class="ac-th"></th>
                    </tr>
                </thead>
                <tbody>${stackRows}</tbody>
            </table>
            <p class="ac-tol-note">
                Worst case adds all tolerances linearly (conservative).
                RSS uses root-sum-of-squares (realistic for production).
                Flag ⚠ when worst-case tolerance exceeds ±1.0mm.
            </p>
        </div>`;

    document.getElementById('assembly-check-btn').hidden = false;
}