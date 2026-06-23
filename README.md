# CAD Auditor

Automated injection molding DFM reviewer. Accepts an STL file and a pull direction, runs five deterministic geometry checks, and produces a structured report with LLM-generated manufacturing guidance before tooling begins.

## The Problem

Injection molding tooling costs between $10,000 and $100,000 and cannot be revised cheaply once cut. Most DFM failures are detectable from geometry alone: insufficient draft causes parts to stick in the mold on ejection, inconsistent wall thickness creates sink marks and internal voids, and undercuts trap the mold steel in a way that requires expensive side actions or part redesign. For entrepreneurs and small product teams without in-house tooling engineers, this review either does not happen or happens after tooling is already committed. CAD Auditor automates the geometric check that catches these failures before the file reaches a manufacturer.

## What It Catches

| Check | What it measures | Default threshold |
|---|---|---|
| Draft angles | Face normal alignment vs. pull direction | Less than 1.0 degree flagged |
| Wall thickness | Ray casting distance through solid | Less than 1.5mm or greater than 4.0mm flagged |
| Undercuts | Face normals opposing pull direction | More than 15 degrees past perpendicular |
| Rib thickness proxy | Local thickness vs. nominal wall ratio | Greater than 60% of nominal wall flagged |
| Sharp corners | Interior dihedral angle at mesh edges | Less than 45 degrees flagged |

## Architecture and Design Decisions

Each decision below names the alternative that was rejected and the reason it lost.

**Input format: STL over STEP.**
STL represents a 3D surface as triangles with normals and nothing more. STEP is richer and preserves feature semantics, tolerances, and parametric constraints. STEP was rejected because parsing it in Python requires pythonOCC, which wraps OpenCASCADE (a C++ geometry kernel) and has notoriously difficult installation on Windows. STL exports from every CAD tool in two clicks, installs via a single pip command through trimesh, and is sufficient for all five geometric checks implemented here. The limitation is documented: rib detection from STL is a proxy because the format carries no feature labels.

**Geometry library: trimesh over Open3D and VTK.**
Open3D is strong for point cloud processing but its mesh analysis is secondary. VTK is industrial grade but requires verbose object-pipeline setup for simple queries. trimesh is purpose-built for mesh analysis, provides face normals automatically on load, integrates ray casting directly with mesh queries, and installs cleanly on Windows. One dependency gap was discovered during development: trimesh's ray casting requires the rtree package, which wraps libspatialindex, and does not pull it in automatically. This is documented in the install instructions.

**Separation of geometry and interpretation.**
The model never computes geometry. Draft angles are trigonometry: the dot product of a face normal with the pull direction vector, passed through arcsin, yields the draft angle in degrees. Wall thickness is geometry: a ray cast inward from the surface returns the distance to the opposite wall. All five checks produce structured Python dicts with counts, percentages, and measurements before the model sees anything. The model receives those numbers and produces natural language interpretation. This means geometric findings are fully deterministic and reproducible regardless of model behavior. Swapping the model or changing the prompt cannot alter a measurement.

**Pull direction as required user input.**
Every geometric check depends on the pull direction, the axis along which the mold opens. It cannot be reliably inferred from STL geometry alone: determining the optimal pull direction for a complex part is a research problem in computational geometry that tools with full CAD kernel access have not fully solved. Requiring the user to specify it forces an explicit manufacturing intent decision before analysis runs. The default is Z, handling the most common case without input on simple geometries.

**Face indices stripped from the LLM prompt.**
The initial implementation serialized the full findings dict to JSON and passed it to the model. On a 335,930 face mesh, the flagged face indices field in the draft check alone produced a prompt of 1,120,344 tokens, exceeding the context window. Face indices carry no interpretable meaning for a language model. The fix strips index lists before serialization and passes only counts, percentages, measurements, and thresholds. The full index data is preserved in the JSON output file for downstream use.

**Output format: JSON plus markdown.**
Each run produces two files. The JSON contains every numeric finding, threshold, severity label, methodology note, and the full LLM interpretation in a machine-readable structure. The markdown renders the same content for human reading. JSON was chosen as the primary artifact because it is composable: a downstream system can ingest findings from multiple parts, compare across design iterations, or feed results into a web interface without parsing prose.

## Install and Run

```bash
git clone https://github.com/by-carrot/cad-auditor
cd cad-auditor
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add your Anthropic API key:ANTHROPIC_API_KEY=your_key_here

Run against an STL file:

```bash
python -m src.main --file path/to/part.stl --pull-direction Z
```

To skip LLM interpretation and run geometry checks only:

```bash
python -m src.main --file path/to/part.stl --pull-direction Z --no-interpret
```

Output files are written to the `output/` directory.

**Note on installation:** trimesh's ray casting engine requires the `rtree` package, which is not installed automatically. If you see `ModuleNotFoundError: No module named 'rtree'`, run `pip install rtree`.

## Run the Tests

```bash
pytest tests/ -v
```

Expected output: 56 passed in under 1 second.

## Project Structure
cad-auditor/

├── src/

│   ├── main.py              CLI entry point

│   ├── load_geometry.py     STL validation and mesh loading

│   ├── draft_check.py       Draft angle analysis

│   ├── thickness_check.py   Wall thickness via ray casting

│   ├── undercut_check.py    Undercut detection

│   ├── feature_check.py     Rib proxy and sharp corner checks

│   ├── aggregate.py         Orchestrates all five checks

│   ├── interpret.py         Anthropic SDK call and prompt design

│   └── report.py            JSON and markdown output

├── tests/

│   └── test_geometry_checks.py

├── eval/

│   └── cases.json

├── sample_stl/

├── requirements.txt

└── README.md

## Evaluation Results

Formal evaluation is in progress. Current results cover two runs.

**Test box (30 x 20 x 10mm solid box, 12 faces):** All five checks produced expected results. Draft flagged all four side walls at zero degrees. Thickness reported approximately 10mm through the solid, above the 4mm maximum. Undercuts flagged the bottom face at alignment score of negative 1.0 against Z pull. Sharp corners passed at 90 degrees above the 45 degree threshold. Rib proxy flagged 100% of samples above the 4.17mm threshold. Overall severity HIGH as expected for a solid rectangular block.

**Real casing part (90 x 35 x 110mm, 335,930 faces):** 26.0% of faces flagged for draft violations. 40.6% flagged as potential undercuts, which on a casing with internal geometry and mating features likely includes intentional undercuts accommodated in the tooling design. Maximum measured thickness of 91.38mm indicates an uncored solid region. 167 sharp edges below the 45 degree threshold. This run also revealed a context window overflow bug on dense meshes, fixed in the face index stripping commit.

**Known limitations reported honestly:**
- Rib detection is a thickness distribution proxy. True rib identification requires parametric CAD feature data not present in STL format.
- Undercut detection is a first order approximation based on face normal alignment. Full shadow volume computation is out of scope.
- Wall thickness uses sampling (500 points by default). Localized thin regions between sample points may be missed on very complex geometry.
- Pull direction must be specified by the user. The tool does not infer it.
- STL carries no unit metadata. The tool assumes millimeters, which is the injection molding convention.

## Status

**Complete:**
- Five geometry checks with 56 passing tests
- LLM interpretation via Anthropic SDK
- JSON and markdown report output
- CLI with configurable thresholds
- Validated against real part geometry at 335,930 face density

**In progress:**
- Formal eval set with labeled cases and expected severity per check
- Knowledge base for material-specific DFM rules
- README screenshot of a real run