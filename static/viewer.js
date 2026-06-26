import * as THREE from 'three';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// ── Constants ──────────────────────────────────────────────────────────────

const PRIORITY = ['draft_angle', 'undercuts', 'rib_thickness', 'wall_thin', 'wall_thick'];

const CHECK_LABELS = {
    draft_angle:   'Draft Angle',
    undercuts:     'Undercut',
    wall_thin:     'Wall Too Thin',
    wall_thick:    'Wall Too Thick',
    rib_thickness: 'Rib Thickness',
};

// Gradient endpoints per check.
// severe = color at worst measurement, mild = color at threshold.
const GRAD = {
    draft_angle:   { severe: new THREE.Color(0xb91c1c), mild: new THREE.Color(0xfca5a5) },
    undercuts:     { severe: new THREE.Color(0xc2410c), mild: new THREE.Color(0xfed7aa) },
    wall_thin:     { severe: new THREE.Color(0x1e3a8a), mild: new THREE.Color(0xbfdbfe) },
    wall_thick:    { severe: new THREE.Color(0x6b21a8), mild: new THREE.Color(0xe9d5ff) },
    rib_thickness: { severe: new THREE.Color(0x9d174d), mild: new THREE.Color(0xfbcfe8) },
};

const BASE_COLOR = new THREE.Color(0xcbd5e1);

// ── Module state ───────────────────────────────────────────────────────────

let renderer, scene, camera, controls, meshObj, geo;
let baseColors;

// faceMap: Map<faceIdx, Map<checkName, measurement>>
// measurement is angle in degrees (draft) or alignment score (undercuts).
let faceMap = new Map();
let activeLayers = new Set(['draft_angle', 'undercuts']);
let severityFilter  = 1.0;   // 1.0 = show all, 0.05 = worst 5% only
let filterThresholds = {};   // {checkName: cutoff_measurement}
let thresholds = {
    draft_angle: 1.0,
    undercuts:   -0.259,
    wall_min:    1.5,
    wall_max:    4.0,
    rib_threshold: 4.17,
};
let isolationMode = false;
const ISOLATION_HIDE = new THREE.Color(0x1e293b); // matches scene background
let tooltip = null;
let animFrameId = null;
let edgeLines = null;


// ── Color computation ──────────────────────────────────────────────────────

function gradientColor(checkName, measurement) {
    const g = GRAD[checkName];
    if (!g) return BASE_COLOR.clone();

    let t;
    if (checkName === 'draft_angle') {
        const thr = thresholds.draft_angle;
        t = thr > 0 ? Math.min(1, Math.max(0, measurement / thr)) : 0;
    } else if (checkName === 'undercuts') {
        const thr = thresholds.undercuts;
        const range = -1.0 - thr;
        t = range !== 0 ? Math.min(1, Math.max(0, (measurement - (-1.0)) / (-range))) : 0;
    } else if (checkName === 'wall_thin') {
        const minThr = thresholds.wall_min;
        t = minThr > 0 ? Math.min(1, Math.max(0, measurement / minThr)) : 0;
    } else if (checkName === 'wall_thick') {
        const maxThr = thresholds.wall_max;
        t = maxThr > 0 ? Math.min(1, Math.max(0, 1 - (measurement - maxThr) / maxThr)) : 0;
    } else if (checkName === 'rib_thickness') {
        const ribThr = thresholds.rib_threshold;
        t = ribThr > 0 ? Math.min(1, Math.max(0, 1 - (measurement - ribThr) / ribThr)) : 0;
    } else {
        t = 0;
    }

    return g.severe.clone().lerp(g.mild, t);
}


function isLayerActive(checkName) {
    if (checkName === 'wall_thin' || checkName === 'wall_thick') {
        return activeLayers.has('wall_thickness');
    }
    return activeLayers.has(checkName);
}

// ── Color buffer rebuild ───────────────────────────────────────────────────

function rebuildColors() {
    const attr = geo.attributes.color;
    const fill = isolationMode ? ISOLATION_HIDE : BASE_COLOR;
    for (let i = 0; i < attr.array.length; i += 3) {
        attr.array[i]     = fill.r;
        attr.array[i + 1] = fill.g;
        attr.array[i + 2] = fill.b;
    }

    for (const [faceIdx, checkMeasures] of faceMap) {
        let chosen = null;
        for (const name of PRIORITY) {
            if (!checkMeasures.has(name) || !isLayerActive(name)) continue;
            const measurement = checkMeasures.get(name);
            if (!facePassesFilter(name, measurement)) continue;
            chosen = gradientColor(name, measurement);
            break;
        }
        if (!chosen) continue;

        for (let v = 0; v < 3; v++) {
            const i = (faceIdx * 3 + v) * 3;
            attr.array[i]     = chosen.r;
            attr.array[i + 1] = chosen.g;
            attr.array[i + 2] = chosen.b;
        }
    }
    attr.needsUpdate = true;
}


function computeFilterThresholds(ratio) {
    filterThresholds = {};
    for (const checkName of PRIORITY) {
        const measurements = [];
        for (const checkMeasures of faceMap.values()) {
            if (checkMeasures.has(checkName)) {
                measurements.push(checkMeasures.get(checkName));
            }
        }
        if (!measurements.length) continue;

        // Both draft (angle) and undercuts (alignment) are most severe
        // at the lowest values, so sorting ascending and taking the
        // bottom ratio% captures the worst faces for both checks.
        measurements.sort((a, b) => a - b);
        const cutoffIdx = Math.min(
            Math.ceil(measurements.length * ratio) - 1,
            measurements.length - 1
        );
        filterThresholds[checkName] = measurements[cutoffIdx];
    }
}

function facePassesFilter(checkName, measurement) {
    if (!(checkName in filterThresholds)) return true;
    return measurement <= filterThresholds[checkName];
}

// ── Camera animation ───────────────────────────────────────────────────────

function animateCameraTo(endTarget, endPosition, durationFrames = 50) {
    if (animFrameId) cancelAnimationFrame(animFrameId);

    const startTarget = controls.target.clone();
    const startPos    = camera.position.clone();
    let frame = 0;

    function step() {
        frame++;
        const alpha = Math.min(1, frame / durationFrames);
        const ease  = 1 - Math.pow(1 - alpha, 3); // cubic ease-out

        camera.position.lerpVectors(startPos, endPosition, ease);
        controls.target.lerpVectors(startTarget, endTarget, ease);
        controls.update();

        if (frame < durationFrames) {
            animFrameId = requestAnimationFrame(step);
        }
    }
    animFrameId = requestAnimationFrame(step);
}


// ── Public: focus camera on a check's flagged faces ────────────────────────

export function focusOnCheck(checkName) {
    if (!geo || !meshObj) return;

    // Sharp corners use edge geometry, not faceMap
    if (checkName === 'sharp_corners' && edgeLines) {
        const pos = edgeLines.geometry.attributes.position;
        if (pos.count === 0) return;
        const box = new THREE.Box3();
        for (let i = 0; i < pos.count; i++) {
            box.expandByPoint(new THREE.Vector3(
                pos.getX(i), pos.getY(i), pos.getZ(i)
            ));
        }
        const center = new THREE.Vector3();
        const size   = new THREE.Vector3();
        box.getCenter(center);
        box.getSize(size);
        const maxDim = Math.max(size.x, size.y, size.z);
        const dir    = camera.position.clone().sub(controls.target).normalize();
        animateCameraTo(center, center.clone().add(dir.multiplyScalar(Math.max(maxDim * 2.2, 40))));
        return;
    }

    const flaggedIndices = [];
    for (const [faceIdx, checkMeasures] of faceMap) {
        if (checkMeasures.has(checkName) && isLayerActive(checkName)) {
            flaggedIndices.push(faceIdx);
        }
    }
    if (flaggedIndices.length === 0) return;

    const step   = Math.ceil(flaggedIndices.length / 1000);
    const sample = flaggedIndices.filter((_, i) => i % step === 0);
    const pos    = geo.attributes.position;
    const scale  = meshObj.scale.x;
    const offset = meshObj.position;
    const box    = new THREE.Box3();

    for (const fi of sample) {
        for (let v = 0; v < 3; v++) {
            const vi = fi * 3 + v;
            box.expandByPoint(new THREE.Vector3(
                pos.getX(vi) * scale + offset.x,
                pos.getY(vi) * scale + offset.y,
                pos.getZ(vi) * scale + offset.z,
            ));
        }
    }

    const center  = new THREE.Vector3();
    const size    = new THREE.Vector3();
    box.getCenter(center);
    box.getSize(size);
    const maxDim  = Math.max(size.x, size.y, size.z);
    const dir     = camera.position.clone().sub(controls.target).normalize();
    animateCameraTo(center, center.clone().add(dir.multiplyScalar(Math.max(maxDim * 2.2, 40))));
}


// ── Public: toggle layer visibility ───────────────────────────────────────

export function toggleLayer(checkName, visible) {
    visible ? activeLayers.add(checkName) : activeLayers.delete(checkName);

    if (checkName === 'sharp_corners') {
        if (edgeLines) edgeLines.visible = visible;
    } else {
        rebuildColors();
    }

    updateLegendVisibility();
}

export function setSeverityFilter(ratio) {
    severityFilter = ratio;
    computeFilterThresholds(ratio);
    rebuildColors();
}

// ── Public: reset camera ──────────────────────────────────────────────────

export function resetCamera() {
    animateCameraTo(new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 60, 200));
}


// ── Public: face counts ────────────────────────────────────────────────────

export function getFaceCounts() {
    const counts = {};
    for (const name of PRIORITY) {
        let n = 0;
        for (const checkMeasures of faceMap.values()) {
            if (checkMeasures.has(name)) n++;
        }
        counts[name] = n;
    }
    return counts;
}

export function setIsolationMode(active) {
    isolationMode = active;
    rebuildColors();
}

// ── Tooltip helpers ────────────────────────────────────────────────────────

function formatMeasurement(checkName, measurement) {
    if (checkName === 'draft_angle') return `${measurement.toFixed(2)}° draft`;
    if (checkName === 'undercuts') {
        const angle = Math.round(Math.acos(Math.abs(measurement)) * 180 / Math.PI);
        return `${angle}° from pull axis`;
    }
    if (checkName === 'wall_thin')     return `${measurement.toFixed(2)}mm — too thin`;
    if (checkName === 'wall_thick')    return `${measurement.toFixed(2)}mm — too thick`;
    if (checkName === 'rib_thickness') return `${measurement.toFixed(2)}mm — rib exceeds ratio`;
    return String(measurement);
}

function showTooltip(x, y, checkName, measurement) {
    if (!tooltip) return;
    tooltip.innerHTML = `
        <span class="tt-check">${CHECK_LABELS[checkName] || checkName}</span>
        <span class="tt-value">${formatMeasurement(checkName, measurement)}</span>`;
    tooltip.style.display = 'block';
    tooltip.style.left    = (x + 14) + 'px';
    tooltip.style.top     = (y - 36) + 'px';
}

function hideTooltip() {
    if (tooltip) tooltip.style.display = 'none';
}

// ── Legend overlay ─────────────────────────────────────────────
let legend = null;

function buildLegend(findings) {
    const el = document.getElementById('viewport-legend');
    if (!el) return;
    legend = el;

    const ch = findings?.checks || {};
    const draftN    = ch.draft_angle?.face_count_flagged                         || 0;
    const undercutN = ch.undercuts?.face_count_flagged                            || 0;
    const sharpN    = ch.sharp_corners?.n_edges_flagged                           || 0;
    const wallN     = (ch.wall_thickness?.thin_face_indices?.length  || 0)
                    + (ch.wall_thickness?.thick_face_indices?.length || 0);
    const ribN      = ch.rib_thickness_proxy?.rib_flagged_face_indices?.length    || 0;

    function cnt(n) { return `<span class="legend-count">${n.toLocaleString()}</span>`; }
    function row(id, grad, top, title, bot, n, check) {
        return `<div class="legend-row" id="${id}" data-check="${check}">
            <div class="legend-grad" style="background:linear-gradient(to bottom,${grad})"></div>
            <div class="legend-labels">
                <span>${top}</span>
                <span class="legend-title">${title}</span>
                <span>${bot}</span>
            </div>
            ${n > 0 ? cnt(n) : ''}
        </div>`;
    }

    el.innerHTML =
        row('lg-draft',    '#b91c1c,#ef4444,#fca5a5', '0°',   'Draft',        `${thresholds.draft_angle.toFixed(1)}°`, draftN,    'draft_angle')
      + row('lg-undercut', '#c2410c,#f97316,#fed7aa', 'opp',  'Undercuts',    'thr',   undercutN, 'undercuts')
      + `<div class="legend-line-row" data-check="sharp_corners" id="lg-sharp">
             <div class="legend-line-swatch"></div>
             <span class="legend-title">Sharp corners</span>
             ${sharpN > 0 ? cnt(sharpN) : ''}
         </div>`
      + row('lg-wall-thin',  '#1e3a8a,#3b82f6,#bfdbfe', '0mm',  'Wall thin',  'min', wallN, 'wall_thickness')
      + row('lg-wall-thick', '#6b21a8,#a855f7,#e9d5ff', 'very', 'Wall thick', 'max', wallN, 'wall_thickness')
      + row('lg-rib',      '#9d174d,#ec4899,#fbcfe8', 'over', 'Rib thickness','thr',   ribN,      'rib_thickness')
      + `<div class="legend-neutral">
             <div class="legend-swatch" style="background:#cbd5e1"></div>
             <span>No issue</span>
         </div>`;

    el.querySelectorAll('[data-check]').forEach(row => {
        row.addEventListener('click', () => {
            const check = row.dataset.check;
            toggleLayer(check, !activeLayers.has(check));
        });
    });

    el.hidden = false;
}

function updateLegendVisibility() {
    if (!legend) return;
    [
        ['lg-draft',      'draft_angle'],
        ['lg-undercut',   'undercuts'],
        ['lg-sharp',      'sharp_corners'],
        ['lg-wall-thin',  'wall_thickness'],
        ['lg-wall-thick', 'wall_thickness'],
        ['lg-rib',        'rib_thickness'],
    ].forEach(([id, check]) => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('layer-off', !activeLayers.has(check));
    });
}

// ── Main init ──────────────────────────────────────────────────────────────

export function initViewer(canvas, stlArrayBuffer, findings) {
    // Dispose previous session
    if (renderer) {
        renderer.dispose();
        if (geo) geo.dispose();
    }
    if (tooltip && tooltip.parentNode) tooltip.parentNode.removeChild(tooltip);
    if (animFrameId) cancelAnimationFrame(animFrameId);

    // Create tooltip element
    tooltip = document.createElement('div');
    tooltip.className = 'viewport-tooltip';
    tooltip.style.display = 'none';
    document.body.appendChild(tooltip);

    faceMap.clear();
    edgeLines = null;
    severityFilter  = 1.0;
    filterThresholds = {};
    activeLayers = new Set(['draft_angle', 'undercuts', 'sharp_corners', 'wall_thickness', 'rib_thickness']);

    // Pull thresholds from live findings
    const draftCheck    = findings.checks.draft_angle;
    const undercutCheck = findings.checks.undercuts;
    const wallCheck     = findings.checks.wall_thickness;
    thresholds.draft_angle   = draftCheck?.threshold_degrees        ?? 1.0;
    thresholds.undercuts     = -(undercutCheck?.opposing_threshold  ?? 0.259);
    thresholds.wall_min      = wallCheck?.threshold_min_mm          ?? 1.5;
    thresholds.wall_max      = wallCheck?.threshold_max_mm          ?? 4.0;
    thresholds.rib_threshold = findings.checks.rib_thickness_proxy?.rib_thickness_threshold_mm ?? 4.17;

    // Build faceMap with per-face measurements
    for (const checkName of PRIORITY) {
        const check = findings.checks[checkName];
        if (!check?.flagged_face_indices?.length) continue;

        const indices      = check.flagged_face_indices;
        const measurements = checkName === 'draft_angle'
            ? check.flagged_face_angles
            : check.flagged_face_alignments;

        for (let i = 0; i < indices.length; i++) {
            const fi = indices[i];
            const m  = measurements?.[i] ?? (checkName === 'draft_angle' ? 0 : -1.0);
            if (!faceMap.has(fi)) faceMap.set(fi, new Map());
            faceMap.get(fi).set(checkName, m);
        }
    }

    // Wall thickness — thin faces
    if (wallCheck?.thin_face_indices?.length) {
        for (let i = 0; i < wallCheck.thin_face_indices.length; i++) {
            const fi = wallCheck.thin_face_indices[i];
            const m  = wallCheck.thin_face_thicknesses?.[i] ?? 0;
            if (!faceMap.has(fi)) faceMap.set(fi, new Map());
            faceMap.get(fi).set('wall_thin', m);
        }
    }

    // Wall thickness — thick faces
    if (wallCheck?.thick_face_indices?.length) {
        for (let i = 0; i < wallCheck.thick_face_indices.length; i++) {
            const fi = wallCheck.thick_face_indices[i];
            const m  = wallCheck.thick_face_thicknesses?.[i] ?? 0;
            if (!faceMap.has(fi)) faceMap.set(fi, new Map());
            faceMap.get(fi).set('wall_thick', m);
        }
    }

    // Rib thickness
    const ribCheck = findings.checks.rib_thickness_proxy;
    if (ribCheck?.rib_flagged_face_indices?.length) {
        for (let i = 0; i < ribCheck.rib_flagged_face_indices.length; i++) {
            const fi = ribCheck.rib_flagged_face_indices[i];
            const m  = ribCheck.rib_flagged_thicknesses?.[i] ?? 0;
            if (!faceMap.has(fi)) faceMap.set(fi, new Map());
            faceMap.get(fi).set('rib_thickness', m);
        }
    }

    // ── Scene ──
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x1e293b);

    const w = canvas.clientWidth  || 820;
    const h = canvas.clientHeight || 440;
    camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 10000);

    renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(w, h, false);

    // ── Lights ──
    scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const sun = new THREE.DirectionalLight(0xffffff, 0.9);
    sun.position.set(1, 2, 1.5);
    scene.add(sun);
    const fill = new THREE.DirectionalLight(0xffffff, 0.25);
    fill.position.set(-1, -1, -1);
    scene.add(fill);

    // ── Parse STL ──
    geo = new STLLoader().parse(stlArrayBuffer);
    geo.computeVertexNormals();

    // ── Base color buffer ──
    const vCount = geo.attributes.position.count;
    baseColors   = new Float32Array(vCount * 3);
    for (let i = 0; i < vCount; i++) {
        baseColors[i * 3]     = BASE_COLOR.r;
        baseColors[i * 3 + 1] = BASE_COLOR.g;
        baseColors[i * 3 + 2] = BASE_COLOR.b;
    }
    geo.setAttribute('color', new THREE.BufferAttribute(baseColors.slice(), 3));
    rebuildColors();

    // ── Mesh ──
    meshObj = new THREE.Mesh(geo, new THREE.MeshPhongMaterial({
        vertexColors: true,
        shininess: 25,
        side: THREE.DoubleSide,
    }));

    geo.computeBoundingBox();
    const box    = geo.boundingBox;
    const center = new THREE.Vector3();
    box.getCenter(center);
    const size   = new THREE.Vector3();
    box.getSize(size);
    const scale  = 100 / Math.max(size.x, size.y, size.z);

    meshObj.position.set(-center.x * scale, -center.y * scale, -center.z * scale);
    meshObj.scale.setScalar(scale);
    scene.add(meshObj);

    // ── Sharp corner edges ────────────────────────────────────
    if (edgeLines) { scene.remove(edgeLines); edgeLines = null; }
    const edgeData = findings.checks.sharp_corners?.flagged_edge_vertices;
    if (edgeData && edgeData.length > 0) {
        const positions = [];
        for (const [v1, v2] of edgeData) {
            positions.push(
                (v1[0] - center.x) * scale,
                (v1[1] - center.y) * scale,
                (v1[2] - center.z) * scale,
                (v2[0] - center.x) * scale,
                (v2[1] - center.y) * scale,
                (v2[2] - center.z) * scale,
            );
        }
        const edgeGeo = new THREE.BufferGeometry();
        edgeGeo.setAttribute('position',
            new THREE.Float32BufferAttribute(positions, 3));
        edgeLines = new THREE.LineSegments(
            edgeGeo,
            new THREE.LineBasicMaterial({ color: 0xfbbf24 })
        );
        edgeLines.visible = activeLayers.has('sharp_corners');
        scene.add(edgeLines);
    }

    // ── Camera & controls ──
    camera.position.set(0, 60, 200);
    camera.lookAt(0, 0, 0);

    controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.dampingFactor = 0.07;
    controls.minDistance   = 15;
    controls.maxDistance   = 600;

    // ── Raycaster (shared for hover and click) ──
    const raycaster  = new THREE.Raycaster();
    const mousePx    = new THREE.Vector2();

    function canvasXY(e) {
        const rect = canvas.getBoundingClientRect();
        return {
            ndcX: ((e.clientX - rect.left) / rect.width)  * 2 - 1,
            ndcY: -((e.clientY - rect.top)  / rect.height) * 2 + 1,
        };
    }

    // Hover: tooltip
    canvas.addEventListener('mousemove', (e) => {
        const { ndcX, ndcY } = canvasXY(e);
        mousePx.set(ndcX, ndcY);
        raycaster.setFromCamera(mousePx, camera);
        const hits = raycaster.intersectObject(meshObj);

        if (!hits.length) { hideTooltip(); return; }

        const checkMeasures = faceMap.get(hits[0].faceIndex);
        if (!checkMeasures)  { hideTooltip(); return; }

        const primary = PRIORITY.find(n => checkMeasures.has(n) && activeLayers.has(n));
        if (!primary)        { hideTooltip(); return; }

        showTooltip(e.clientX, e.clientY, primary, checkMeasures.get(primary));
    });

    canvas.addEventListener('mouseleave', hideTooltip);

    // Click: jump to card
    canvas.addEventListener('click', (e) => {
        const { ndcX, ndcY } = canvasXY(e);
        mousePx.set(ndcX, ndcY);
        raycaster.setFromCamera(mousePx, camera);
        const hits = raycaster.intersectObject(meshObj);
        if (!hits.length) return;

        const checkMeasures = faceMap.get(hits[0].faceIndex);
        if (!checkMeasures) return;

        for (const name of PRIORITY) {
            if (checkMeasures.has(name) && activeLayers.has(name)) {
                const card = document.querySelector(`[data-check="${name}"]`);
                if (card) {
                    card.classList.add('highlighted');
                    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    setTimeout(() => card.classList.remove('highlighted'), 2000);
                }
                break;
            }
        }
    });

    // ── Render loop ──
    (function animate() {
        requestAnimationFrame(animate);
        controls.update();
        renderer.render(scene, camera);
    })();

    // ── Resize ──
    new ResizeObserver(() => {
        const w = canvas.clientWidth;
        const h = canvas.clientHeight;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h, false);
    }).observe(canvas);

    // ── Resize ──
    new ResizeObserver(() => {
        const w = canvas.clientWidth;
        const h = canvas.clientHeight;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h, false);
    }).observe(canvas);

    buildLegend(findings);
}

export function computePullSuggestion(stlArrayBuffer) {
    const geo    = new STLLoader().parse(stlArrayBuffer);
    const pos    = geo.attributes.position;
    const count  = pos.count;
    const scores = { X: 0, Y: 0, Z: 0 };

    for (let i = 0; i < count; i += 3) {
        const ax = pos.getX(i),   ay = pos.getY(i),   az = pos.getZ(i);
        const bx = pos.getX(i+1), by = pos.getY(i+1), bz = pos.getZ(i+1);
        const cx = pos.getX(i+2), cy = pos.getY(i+2), cz = pos.getZ(i+2);

        const abx = bx-ax, aby = by-ay, abz = bz-az;
        const acx = cx-ax, acy = cy-ay, acz = cz-az;
        const nx = aby*acz - abz*acy;
        const ny = abz*acx - abx*acz;
        const nz = abx*acy - aby*acx;

        const area = Math.sqrt(nx*nx + ny*ny + nz*nz) / 2;
        const absX = Math.abs(nx), absY = Math.abs(ny), absZ = Math.abs(nz);
        const max  = Math.max(absX, absY, absZ);
        if (max === absX) scores.X += area;
        else if (max === absY) scores.Y += area;
        else scores.Z += area;
    }

    geo.dispose();

    let best = 'Z', bestScore = 0;
    for (const [axis, score] of Object.entries(scores)) {
        if (score > bestScore) { bestScore = score; best = axis; }
    }
    const total      = scores.X + scores.Y + scores.Z;
    const confidence = total > 0 ? Math.round(bestScore / total * 100) : 0;
    return { suggested: best, confidence };
}