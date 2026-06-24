import * as THREE from 'three';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// Priority order determines which color wins when a face is flagged by both checks.
const PRIORITY   = ['draft_angle', 'undercuts'];
const CHECK_COLORS = {
    draft_angle: new THREE.Color(0xef4444),
    undercuts:   new THREE.Color(0xf97316),
};
const BASE_COLOR = new THREE.Color(0xcbd5e1);

let renderer, scene, camera, controls, meshObj, geo;
let faceMap    = new Map();   // faceIdx → Set<checkName>
let activeLayers = new Set(['draft_angle', 'undercuts']);
let baseColors;               // Float32Array, neutral colors, rebuilt once per init


export function initViewer(canvas, stlArrayBuffer, findings) {
    // Dispose previous renderer if re-initializing
    if (renderer) {
        renderer.dispose();
        if (geo) geo.dispose();
    }

    faceMap.clear();
    activeLayers = new Set(['draft_angle', 'undercuts']);

    // ── Scene ────────────────────────────────────────────
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x1e293b);

    const w = canvas.clientWidth  || 800;
    const h = canvas.clientHeight || 440;
    camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 10000);

    renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(w, h, false);

    // ── Lights ───────────────────────────────────────────
    scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const sun = new THREE.DirectionalLight(0xffffff, 0.9);
    sun.position.set(1, 2, 1.5);
    scene.add(sun);
    const fill = new THREE.DirectionalLight(0xffffff, 0.25);
    fill.position.set(-1, -1, -1);
    scene.add(fill);

    // ── Parse STL ────────────────────────────────────────
    geo = new STLLoader().parse(stlArrayBuffer);
    geo.computeVertexNormals();

    // ── Build face map ───────────────────────────────────
    for (const checkName of PRIORITY) {
        const check = findings.checks[checkName];
        if (!check || !check.flagged_face_indices) continue;
        for (const faceIdx of check.flagged_face_indices) {
            if (!faceMap.has(faceIdx)) faceMap.set(faceIdx, new Set());
            faceMap.get(faceIdx).add(checkName);
        }
    }

    // ── Base color buffer ────────────────────────────────
    const vCount = geo.attributes.position.count;
    baseColors = new Float32Array(vCount * 3);
    for (let i = 0; i < vCount; i++) {
        baseColors[i * 3]     = BASE_COLOR.r;
        baseColors[i * 3 + 1] = BASE_COLOR.g;
        baseColors[i * 3 + 2] = BASE_COLOR.b;
    }

    geo.setAttribute('color', new THREE.BufferAttribute(baseColors.slice(), 3));
    _rebuildColors();

    // ── Mesh ─────────────────────────────────────────────
    meshObj = new THREE.Mesh(geo, new THREE.MeshPhongMaterial({
        vertexColors: true,
        shininess: 25,
        side: THREE.DoubleSide,
    }));

    // Center and normalize scale
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

    // ── Camera & controls ────────────────────────────────
    camera.position.set(0, 60, 200);
    camera.lookAt(0, 0, 0);

    controls = new OrbitControls(camera, canvas);
    controls.enableDamping  = true;
    controls.dampingFactor  = 0.07;
    controls.minDistance    = 15;
    controls.maxDistance    = 600;

    // ── Click → card ─────────────────────────────────────
    const raycaster = new THREE.Raycaster();
    const mouse     = new THREE.Vector2();

    canvas.addEventListener('click', (e) => {
        const rect = canvas.getBoundingClientRect();
        mouse.x =  ((e.clientX - rect.left) / rect.width)  * 2 - 1;
        mouse.y = -((e.clientY - rect.top)  / rect.height) * 2 + 1;
        raycaster.setFromCamera(mouse, camera);

        const hits = raycaster.intersectObject(meshObj);
        if (!hits.length) return;

        const checks = faceMap.get(hits[0].faceIndex);
        if (!checks) return;

        // Highest-priority active check wins
        for (const name of PRIORITY) {
            if (checks.has(name) && activeLayers.has(name)) {
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

    // ── Render loop ──────────────────────────────────────
    (function animate() {
        requestAnimationFrame(animate);
        controls.update();
        renderer.render(scene, camera);
    })();

    // ── Resize ───────────────────────────────────────────
    new ResizeObserver(() => {
        const w = canvas.clientWidth;
        const h = canvas.clientHeight;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h, false);
    }).observe(canvas);
}


export function toggleLayer(checkName, visible) {
    visible ? activeLayers.add(checkName) : activeLayers.delete(checkName);
    _rebuildColors();
}


export function resetCamera() {
    camera.position.set(0, 60, 200);
    camera.lookAt(0, 0, 0);
    controls.reset();
}


function _rebuildColors() {
    const attr = geo.attributes.color;
    attr.array.set(baseColors);

    for (const [faceIdx, checkSet] of faceMap) {
        let chosen = null;
        for (const name of PRIORITY) {
            if (checkSet.has(name) && activeLayers.has(name)) {
                chosen = CHECK_COLORS[name];
                break;
            }
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