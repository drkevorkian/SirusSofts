# Source-checked engineering feasibility (offline prototype)

**State:** GitHub source and synthetic tests only. Not deployed to Gentoo/Ubuntu, not an API or training route, and not a model's demonstrated engineering competence.

This is the first narrow deterministic tool for the roadmap's engineering feasibility and constraint work. The evaluator takes an **operator-defined project and 1–40 checks**, each with an exact approved PDF quote pointer for an observed and limiting measurement, and a directional constraint (\`at_most\`, \`at_least\`, or \`equal\`). The original values are independently re-extracted from the private PDF ingest catalog on every evaluation. Model drafts, prior inference, unreviewed PDFs, invented source IDs and wrong page/part/quote pairs are not trusted as measured facts.

It supports bounded dimensional comparisons for lengths (mm/cm), torques (Nm/ft-lb/in-lb), voltage, current, pressure, resistance and rpm. Other dimensions remain **unsupported**, not silently comparable. Equality means exact arithmetic equality; no engineering tolerance is assumed.

Results distinguish a **verified numeric violation** from **missing or incompatible evidence**, and from **only the supplied numeric checks passing**. They do **not** certify an engine swap or whole-system feasibility. When a requirement is unresolved, the exact operator-authored question appears in \`questions_requiring_evidence\`. The evaluator does not infer whether a donor part applies to a particular year/model; source-to-vehicle applicability, mount geometry, ECU/harness, driveline, cooling, load cases, regulatory considerations and fabrication plans still need independent checks. In particular, an oversized donor does not prove the whole project is impossible; it identifies one present constraint that may require redesign.

Example (using placeholder source IDs; replace with existing **approved** private PDF documents; no example claims real Spyder/Commander dimensions):

\`\`\`python
from feasibility_review import assess

report = assess({
  "project": "donor engine fitment concept",
  "checks": [{
    "id": "width",
    "question": "Is engine envelope width within chassis bay width?",
    "criterion": "at_most",
    "actual": {"source_id": "pdf:" + "a"*64, "page": 2, "part": 1,
               "quote": "Engine width: 80 cm"},
    "limit": {"source_id": "pdf:" + "b"*64, "page": 8, "part": 1,
              "quote": "Available bay width: 750 mm"}
  }]
}, source=Path("/path/to/private/pdf_ingest.sqlite3"))
\`\`\`

No network connections, file writes, database changes, model-weight changes or executable actions occur. Callers should keep resulting source identifiers and quotations private. This module is not connected to the existing chat or research job controller. Do **not** mark full cross-document reasoning, automatic evidence-gap discovery, or real modification feasibility complete until application validation, question generation from the actual corpus, and live model evaluations have passed.
