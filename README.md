# CAD Auditor

DFM reviewer for injection molding and resin casting. Upload an STL file, select your material, prototype method, and production method, and get a structured report separating what needs fixing before your first prototype from what needs fixing before production tooling — with a full-width 3D viewport showing exactly which faces and edges are flagged, colored by check type and severity.

## The Problem

Injection molding tooling costs between $10,000 and $100,000 and cannot be revised cheaply once cut. Resin casting silicone molds cost $300 to $1,500 but produce parts whose quality is entirely determined by design decisions made before the first pour. Most DFM failures in both processes are detectable from geometry alone: zero draft causes parts to seize in injection molds, undercuts add $2,000 to $8,000 per side action, and inconsistent wall thickness causes sink marks on cosmetic surfaces regardless of manufacturing method. For first-time product entrepreneurs without in-house tooling engineers, this review either does not happen or happens after money is already committed. CAD Auditor automates the geometric check that catches these failures while the cost of fixing them is still zero.

## Dashboard

The browser dashboard renders after upload. No setup required beyond cloning the repo and running the server.

![Dashboard showing HIGH severity result with 3D viewport, check cards ordered by severity, and DFM assessment](docs/dashboard.png)

## What It Catches

| Check | What it measures | Injection molding threshold | Resin casting threshold | 3D visualization |
|---|---|---|---|---|
| Draft angles | Face normal alignment vs. pull direction | Less than 1.0° flagged (material-adjusted) | Advisory only — silicone releases without draft | Red gradient per face |
| Wall thickness | Ray casting distance through solid | Less than min or greater than max per material | Less than 0.5mm flagged | Blue (thin) and purple (thick) per sampled face |
| Undercuts | Face normals opposing pull direction | More than 15° past perpendicular — side action required | Acceptable — silicone stretches over undercuts | Orange gradient per face |
| Rib thickness proxy | Local thickness vs. nominal wall ratio | Greater than 60% of nominal wall | Same threshold — sink marks occur in both processes | Pink per sampled face |
| Sharp corners | Interior dihedral angle at mesh edges | Less than 45° flagged | Same threshold — stress concentration in cured resin | Yellow lines along flagged edges |

All five checks run on every submission regardless of production method. Severity labels are overridden per-check based on what actually matters for the chosen process. Material selection adjusts wall thickness and draft angle thresholds for ABS, PP, PC, Nylon PA6, and TPE.

## Architecture and Design Decisions

Each decision below names the alternative that was rejected and the reason it lost.

**Input format: STL over STEP.**
STL represents a 3D surface as triangles with normals and nothing more. STEP is richer and preserves feature semantics, tolerances, and parametric constraints. STEP was rejected because parsing it requires pythonOCC, which wraps OpenCASCADE and has notoriously difficult installation on Windows. STL exports from every CAD tool in two clicks, installs cleanly via trimesh, and is sufficient for all five geometric checks. Limitation: rib detection from STL is a proxy because the format carries no feature labels.

**Geometry library: trimesh over Open3D and VTK.**
Open3D is strong for point cloud processing but its mesh analysis is secondary. VTK is industrial grade but requires verbose object-pipeline setup for simple queries. trimesh is purpose-built for mesh analysis, provides face normals automatically on load, integrates ray casting directly, and installs cleanly on Windows. One dependency gap was discovered during development: trimesh's ray casting requires the `rtree` package, which it does not pull in automatically.

**Separation of geometry and interpretation.**
The model never computes geometry. Draft angles are trigonometry: the dot product of a face normal with the pull direction, passed through arcsin. Wall thickness is geometry: a ray cast inward from the surface returns the distance to the opposite wall. All five checks produce structured Python dicts with counts, percentages, and measurements before the model sees anything. The model receives those numbers and produces natural language interpretation. Geometric findings are fully deterministic and reproducible regardless of model behavior. Swapping the model or changing the prompt cannot alter a measurement.

**Face indices stripped from the LLM prompt but preserved in the API response.**
On a 335,930 face mesh, the flagged face index list from the draft check alone produced a prompt of 1,120,344 tokens, exceeding the context window. Face indices carry no interpretable meaning for a language model. The fix strips index lists before serialization to the prompt but preserves them in the API JSON response so the 3D viewport renderer can color individual flagged faces. This is a clean separation: the LLM never sees raw geometry references, and the 3D renderer never needs to make its own API call.

**Web layer: FastAPI over Flask.**
FastAPI was chosen because it uses Pydantic for automatic request and response validation, generates API documentation automatically, and handles file uploads via `python-multipart` with minimal configuration. The geometry pipeline runs synchronously inside the endpoint because trimesh and numpy operations are CPU-bound, not I/O-bound. Using `async def` with CPU-bound work blocks the event loop. For a local server with one user at a time, synchronous is correct.

**Two-stage reporting design.**
The report separates findings into two sections: what needs fixing before the current prototype attempt, and what needs fixing before production tooling. The split is governed by the chosen prototype method. SLS, FDM, and resin printing are forgiving on draft, undercuts, and wall thickness down to 0.5mm to 1.0mm depending on process. A single list of findings ordered only by severity produces noise that erodes user trust: a draft violation that is irrelevant to the SLS prototype and critical to the injection mold should be clearly labeled, not mixed with findings that need action immediately.

**Production method severity overrides.**
The geometry pipeline runs against fixed thresholds because those are the defaults. For resin casting, the same geometry produces different risk profiles: draft violations are advisory because silicone releases without draft, undercut findings are non-issues because silicone stretches over them, and wall thickness minimum drops to 0.5mm. The `stage.py` module computes `effective_severity` for each check based on the production method selected. The severity banner uses `overall_effective_severity` not raw geometry severity, so a resin casting analysis on a part with only draft and undercut violations correctly shows a lower severity than the same part analyzed for injection molding.

**Material-specific thresholds.**
Wall thickness and draft angle thresholds vary significantly by resin. PP minimum wall is 0.8mm versus 1.5mm for ABS. PC minimum draft is 1.5° versus 1.0° for ABS. TPE requires 3.0° minimum draft because soft materials grip mold surfaces harder during ejection. The `materials.json` file stores five material profiles. Selecting a material at upload time recalibrates all five checks through `aggregate.run_all_checks()` without touching any check module. An Advanced section in the upload form allows manual override of individual thresholds on top of the material defaults. The CLI defaults to ABS and remains backward-compatible.

**Per-face severity gradient over binary coloring.**
Early versions colored all flagged faces the same flat color per check. This gave no information about which violations were worst. The current implementation passes per-face measurements alongside face indices in the API response: `flagged_face_angles` for draft, `flagged_face_alignments` for undercuts, `thin_face_thicknesses` and `thick_face_thicknesses` for wall thickness, `rib_flagged_thicknesses` for rib proxy, and `flagged_edge_vertices` for sharp corners. The Three.js viewer interpolates between a severe and mild color endpoint based on each face's actual measurement, producing a gradient that immediately communicates which regions need the most attention.

**Pull direction suggestion from geometry.**
Most first-time users do not know their pull direction. The viewer parses the STL in the browser before upload, computes face-area-weighted surface normal distribution per axis, and suggests the axis with the largest flat-opposing surface area as the probable pull direction. The suggestion updates the dropdown automatically on file selection with a confidence percentage. The user can override it. This runs client-side with no server call.

**Knowledge base: structured JSON injection over RAG.**
The knowledge base covers fourteen injection molding topics and twelve resin casting topics across four JSON files. RAG was evaluated and rejected. The domain is compact and fully known in advance. RAG adds retrieval latency, embedding cost, a vector database dependency, and non-deterministic chunk selection for a knowledge corpus that fits comfortably in a single context window. Structured injection is deterministic, auditable, and gives complete control over what the model sees. The loader selects which rules to include based on which checks flagged and which production method is active.

**Actionable Fusion 360 fix instructions per finding.**
The interpretation prompt instructs the model to end every flagged finding with a specific Fusion 360 tool path using the actual measurements from the analysis. A draft violation on a Z-pull ABS part produces: "Fusion 360: Modify > Draft > select flagged face regions > set Pull Direction to Z axis > apply 1.0° minimum." Generic DFM advice that requires the designer to search documentation adds friction between the report and the fix. Embedding the exact menu path removes that friction.

**IP protection: measurements only reach the API.**
The STL file is written to a temporary server path, processed, and deleted in a `finally` block regardless of whether processing succeeds or fails. The geometry pipeline extracts counts, percentages, measurements, and severity labels. Only those structured measurements are serialized to the Anthropic API. Raw geometry, face coordinates, and vertex data never leave the server.

**Material preview mode: PBR materials over custom shaders.**
The viewer supports a material preview mode that replaces the severity-colored mesh with a `MeshStandardMaterial` using physically-based rendering properties calibrated to specific production materials: ceramic-coated finish (roughness 0.93), polyurethane resin cast (roughness 0.72), zamak die cast (metalness 0.88, roughness 0.18), and CNC machined resin prototype (roughness 0.48). Zamak uses Three.js `RoomEnvironment` via `PMREMGenerator` for accurate metallic reflections with no external HDR file required. Matte presets skip environment mapping. Switching back to DFM view restores the vertex color buffer exactly.

## Install and Run

### Web dashboard (recommended)

```bash
git clone https://github.com/by-carrot/cad-auditor
cd cad-auditor
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add your Anthropic API key:

```
ANTHROPIC_API_KEY=your_key_here
```

Start the server:

```bash
uvicorn src.web.app:app --reload
```

Open `http://127.0.0.1:8000` in your browser. Upload an STL file, select your material, prototype method, and production method, and click Analyze.

**Note on installation:** trimesh's ray casting engine requires the `rtree` package, which is not pulled in automatically. If you see `ModuleNotFoundError: No module named 'rtree'`, run `pip install rtree`.

**Running without an API key:** The geometry pipeline runs fully without an Anthropic API key. The DFM assessment section will show a placeholder. All five geometry checks, the 3D viewport, severity coloring, and check cards render normally.

### CLI (geometry checks without web interface)

```bash
python -m src.main --file path/to/part.stl --pull-direction Z
```

To skip LLM interpretation and run geometry checks only:

```bash
python -m src.main --file path/to/part.stl --pull-direction Z --no-interpret
```

CLI output is written to the `output/` directory as JSON and markdown.

## Run the Tests

```bash
pytest tests/ -v
```

Expected output: 56 passed in under 1 second. All tests cover the deterministic geometry layer. The web layer and LLM interpretation are not tested because they depend on external state.

## Project Structure

```
cad-auditor/
├── src/
│   ├── main.py                  CLI entry point
│   ├── load_geometry.py         STL validation and mesh loading
│   ├── draft_check.py           Draft angle analysis with per-face angle output
│   ├── thickness_check.py       Wall thickness via ray casting with per-sample face indices
│   ├── undercut_check.py        Undercut detection with per-face alignment output
│   ├── feature_check.py         Rib proxy and sharp corner checks with edge vertex output
│   ├── aggregate.py             Orchestrates all five checks with material-specific thresholds
│   ├── interpret.py             Anthropic SDK, two-stage prompt, Fusion 360 fix instructions
│   ├── report.py                JSON and markdown output (CLI only)
│   ├── stage.py                 Stage relevance labels and production method severity overrides
│   ├── knowledge/
│   │   ├── loader.py            Builds knowledge context, selects by production method and material
│   │   └── data/
│   │       ├── dfm_rules.json           14-entry injection molding knowledge base
│   │       ├── materials.json           5 plastic material profiles with thresholds
│   │       ├── collectibles.json        Collectible form object specific rules
│   │       └── resin_casting_rules.json 12-entry resin casting knowledge base
│   └── web/
│       ├── __init__.py
│       └── app.py               FastAPI application and /analyze endpoint
├── static/
│   ├── index.html               Single-page dashboard
│   ├── style.css                Dashboard styling
│   ├── app.js                   Upload form, analysis flow, results rendering
│   └── viewer.js                Three.js 3D viewport, all five check visualizations, material preview
├── tests/
│   └── test_geometry_checks.py
├── eval/
│   └── cases.json               4-case labeled evaluation set
├── sample_stl/
├── docs/
│   └── dashboard.png
├── requirements.txt
└── README.md
```

## 3D Viewport

The viewport renders the STL mesh in the browser using Three.js loaded from a CDN via importmap. No build step required.

**Color encoding:** Draft violations are a red gradient from deep red at 0° to light pink near the threshold. Undercuts are an orange gradient. Wall thickness thin violations are a blue gradient. Wall thickness thick violations are a purple gradient. Rib thickness violations are a pink gradient. Sharp corner edges are yellow `LineSegments` overlaid on the mesh.

**Interactive legend:** The legend in the bottom-left corner shows all five check types with face counts. Clicking any legend row toggles that check's visualization on and off.

**Controls:** Drag to rotate. Scroll to zoom. Ctrl+drag to pan. Click any flagged face to highlight and scroll to the corresponding finding card. Click any finding card to animate the camera to frame that check's flagged region.

**Isolation mode:** The Isolate flagged button hides all unflagged geometry. Useful for dense meshes where a large percentage of faces are flagged.

**Severity filter slider:** The Worst slider trims the viewport to show only the N% most severe faces per check. At 10%, only the most critically undrafted faces remain colored.

**Material preview mode:** The Material preview button switches the mesh from severity coloring to a PBR material simulation across four production material presets.

## Evaluation Results

**Test box (30 x 20 x 10mm solid box, 12 faces):** All five checks produced expected results. Overall severity HIGH as expected for a solid rectangular block.

**Real casing part (90 x 35 x 110mm, 335,930 faces):** 26.0% of faces flagged for draft violations. 40.6% flagged as potential undercuts. Maximum measured thickness of 91.38mm indicates an uncored solid region. 167 sharp edges below the 45 degree threshold.

**Known limitations reported honestly:**
- Rib detection is a thickness distribution proxy. True rib identification requires parametric CAD feature data not present in STL format.
- Undercut detection is a first-order approximation based on face normal alignment. Full shadow volume computation is out of scope.
- Wall thickness and rib proxy use sampling (500 points by default). Sampled face indices are visualized but do not constitute complete coverage of all violations.
- Pull direction suggestion is a surface area heuristic and should be verified before accepting.
- STL carries no unit metadata. The tool assumes millimeters.

## Knowledge Base Sources

Injection molding rules are sourced from: Protolabs design tips library, Fictiv injection molding design guide, ZetarMold gate types guide, Xometry surface finish reference, Sussex IM gate types guide, Weilin Plastic venting design guide, and Malloy, *Plastic Part Design for Injection Molding*, Hanser, 2nd ed. 2010.

Resin casting rules are sourced from: WayKen vacuum casting design guide, SyBridge Technologies critical design guidelines for urethane casting, Formlabs vacuum casting guide, GD Prototyping Shore hardness chart, RAMPF/Innovative Polymers painting cast urethane parts guide, FacFox design tips for urethane casting, and Wortmann et al. 2022, *Industrial-Scale Vacuum Casting with Silicone Molds: A Review*, Applied Research, Wiley.

## Status

**Complete:**
- Five geometry checks with 56 passing tests and 4-case eval set
- LLM interpretation with two-stage prompt, material context, and Fusion 360 fix instructions
- Mock interpretation when API key absent
- FastAPI web layer with /analyze endpoint
- Material selector: ABS, PP, PC, Nylon PA6, TPE with per-material thresholds
- Configurable threshold overrides in Advanced section
- Pull direction suggestion from geometry computed client-side
- Two-stage report separating prototype from production findings
- Production method selection: injection molding and resin casting with severity overrides
- Severity banner using effective post-override severity
- Three.js 3D viewport with per-face severity gradient for all five checks
- Interactive legend with face counts doubling as layer toggles
- Isolation mode, severity filter slider, camera zoom, hover tooltips
- Sharp corner edge highlighting as yellow LineSegments
- Material preview mode: ceramic coated, resin cast, zamak die cast, CNC prototype
- Injection molding knowledge base: 14 sourced entries
- Resin casting knowledge base: 12 sourced entries
- Validated against real part at 335,930 face density
- IP protection: STL deleted after processing, only measurements reach Anthropic API

**Planned:**
- Version comparison: diff two analyses to show which issues improved after redesign
- Resin casting as selectable prototype method
- PDF report download
- Boss and weld line detection as dedicated geometry checks
- Deployed demo URL
