import * as THREE from 'three';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// ── Constants ──────────────────────────────────────────────────────────────

const PRIORITY = ['draft_angle', 'undercuts'];

const CHECK_LABELS = {
    draft_angle: 'Draft Angle',
    undercuts:   'Undercut',
};

// Gradient endpoints per check.
// severe = color at worst measurement, mild = color at threshold.
const GRAD = {
    draft_angle: {
        severe: new THREE.Color(0xb91c1c),
        mild:   new THREE.Color(0xfca5a5),
    },
    undercuts: {
        severe: new THREE.Color(0xc2410c),
        mild:   new THREE.Color(0xfed7aa),
    },
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
let thresholds = { draft_angle: 1.0, undercuts: -0.259 };
let isolationMode = false;
const ISOLATION_HIDE = new THREE.Color(0x1e293b); // matches scene background
let tooltip = null;
let animFrameId = null;
let edgeLines = null;


// ── Color computation ──────────────────────────────────────────────────────

function gradientColor(checkName, measurement) {
    const g = GRAD[checkName];
    if (!g) return BASE_COLOR.clone();

    let t; // 0 = most severe (dark), 1 = least severe (light)
    if (checkName === 'draft_angle') {
        const thr = thresholds.draft_angle;
        t = thr > 0 ? Math.min(1, Math.max(0, measurement / thr)) : 0;
    } else {
        // undercuts: alignment from -1.0 (severe) to threshold (mild)
        const thr = thresholds.undercuts; // e.g. -0.259
        const range = -1.0 - thr;        // e.g. -0.741  (negative)
        t = range !== 0
            ? Math.min(1, Math.max(0, (measurement - (-1.0)) / (-range)))
            : 0;
    }

    return g.severe.clone().lerp(g.mild, t);
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
            if (!checkMeasures.has(name) || !activeLayers.has(name)) continue;
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

    // Collect all face indices flagged by this check
    const flaggedIndices = [];
    for (const [faceIdx, checkMeasures] of faceMap) {
        if (checkMeasures.has(checkName) && activeLayers.has(checkName)) {
            flaggedIndices.push(faceIdx);
        }
    }
    if (flaggedIndices.length === 0) return;

    // Sample up to 1000 faces for bounding box calculation
    const step    = Math.ceil(flaggedIndices.length / 1000);
    const sample  = flaggedIndices.filter((_, i) => i % step === 0);
    const pos     = geo.attributes.position;
    const scale   = meshObj.scale.x;
    const offset  = meshObj.position;
    const box     = new THREE.Box3();

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

    // Place camera in its current direction, at a distance that frames the region
    const dir     = camera.position.clone().sub(controls.target).normalize();
    const endPos  = center.clone().add(dir.multiplyScalar(Math.max(maxDim * 2.2, 40)));

    animateCameraTo(center, endPos);
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
    if (checkName === 'draft_angle') {
        return `${measurement.toFixed(2)}° draft`;
    }
    if (checkName === 'undercuts') {
        const angle = Math.round(Math.acos(Math.abs(measurement)) * 180 / Math.PI);
        return `${angle}° from pull axis`;
    }
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

function buildLegend(canvas) {
    if (legend && legend.parentNode) legend.parentNode.removeChild(legend);

    const rect = canvas.getBoundingClientRect();
    legend = document.createElement('div');
    legend.className = 'viewport-legend';
    legend.innerHTML = `
        <div class="legend-row">
            <div class="legend-grad" style="background: linear-gradient(to bottom,
                #b91c1c, #ef4444, #fca5a5)"></div>
            <div class="legend-labels">
                <span>0°</span>
                <span class="legend-title">Draft</span>
                <span>${thresholds.draft_angle.toFixed(1)}°</span>
            </div>
        </div>
        <div class="legend-row" id="legend-sharp">
            <div class="legend-grad" style="background:#fbbf24; min-height:12px; border-radius:3px;"></div>
            <div class="legend-labels">
                <span style="font-size:10px; color:#94a3b8; font-family:var(--font-mono, monospace);">Lines</span>
                <span class="legend-title">Sharp corners</span>
                <span></span>
            </div>
        </div>
        </div>
        <div class="legend-neutral">
            <div class="legend-swatch" style="background:#cbd5e1"></div>
            <span>No issue</span>
        </div>`;
    document.querySelector('.viewport-section').appendChild(legend);
}

function updateLegendVisibility() {
    if (!legend) return;
    const draftRow  = legend.querySelector('.legend-row:first-child');
    const undercutRow = document.getElementById('legend-undercuts');
    const sharpRow    = document.getElementById('legend-sharp');
    if (draftRow)    draftRow.style.display    = activeLayers.has('draft_angle')   ? 'flex' : 'none';
    if (undercutRow) undercutRow.style.display = activeLayers.has('undercuts')     ? 'flex' : 'none';
    if (sharpRow)    sharpRow.style.display    = activeLayers.has('sharp_corners') ? 'flex' : 'none';
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
    activeLayers = new Set(['draft_angle', 'undercuts', 'sharp_corners']);

    // Pull thresholds from live findings
    const draftCheck    = findings.checks.draft_angle;
    const undercutCheck = findings.checks.undercuts;
    thresholds.draft_angle = draftCheck?.threshold_degrees        ?? 1.0;
    thresholds.undercuts   = -(undercutCheck?.opposing_threshold  ?? 0.259);

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