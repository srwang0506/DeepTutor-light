"""Read-only experiment ingestion and rebuildable, provenance-bound artifacts."""

import csv
import io
import json
from pathlib import Path

from .database import Conflict, encoded, sha
from .evidence import compare_observations
from .models import RunInput


def create_run(
    store,
    command: RunInput,
    identifier=None,
    expected=None,
    *,
    provenance=None,
    sources=(),
    import_identity=None,
):
    data = command.model_dump()
    required = (
        "repository",
        "commit",
        "command",
        "configuration",
        "dataset",
        "split",
        "environment",
        "hardware",
        "seed",
        "checkpoint",
        "log",
    )
    data["missing"] = [
        field for field in required if data[field] is None or data[field] == "" or data[field] == {}
    ]
    # Even a complete metadata manifest does not prove inputs remain obtainable.
    data["reproducibility"] = (
        "incomplete" if data["missing"] else "metadata_complete_inputs_unverified"
    )
    data["log_hash"] = store.save_blob(command.log.encode()) if command.log else None
    data["patch_hash"] = store.save_blob(command.patch.encode()) if command.patch else None
    if provenance:
        data.update(provenance)
    with store.db(write=True) as db:
        if import_identity:
            key, payload_hash = import_identity
            existing = db.execute(
                "SELECT id FROM records WHERE kind='run' AND scope=? AND json_extract(data,'$.import_key')=?",
                (store.project, key),
            ).fetchone()
            if existing:
                record = store.record(db, existing[0], "run")
                if record["data"]["import_payload_hash"] != payload_hash:
                    raise Conflict(
                        "This import key was already used with different content. Use a new snapshot identity."
                    )
                return record
            data.update(import_key=key, import_payload_hash=payload_hash)
        if identifier:
            previous = store.record(db, identifier, "run")["data"]
            for field in ("remote_source", "execution_verified", "tracking_snapshot"):
                if field in previous:
                    data[field] = previous[field]
        row = store.put(db, "run", data, identifier=identifier, expected=expected)
        for source, relation in sources:
            store.link(db, source, row["id"], relation)
        return row


def tex_escape(text):
    chars = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(chars.get(c, c) for c in str(text))


def csv_cell(value):
    text = str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def make_table(store, title, identifiers):
    if not 1 <= len(identifiers) <= 100 or len(set(identifiers)) != len(identifiers):
        raise ValueError("Select 1–100 distinct observations.")
    rows = [store.get(i, "observation") for i in identifiers]
    comparisons = [
        {"left": identifiers[0], "right": i, **compare_observations(store, identifiers[0], i)}
        for i in identifiers[1:]
    ]
    comparable = len(rows) > 1 and all(c["status"] == "comparable" for c in comparisons)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Method", "Value", "State", "Origin", "Metric", "Unit", "Source", "Revision"])
    latex = [r"\begin{tabular}{lrll}", r"Method & Value & Unit & Status \\", r"\hline"]
    for row in rows:
        d = row["data"]
        value = d["value"] if d["value"] is not None else ""
        writer.writerow(
            [
                csv_cell(d["title"]),
                value,
                d["state"],
                d["origin"],
                csv_cell(d["protocol"]["metric"]),
                csv_cell(d["protocol"]["unit"]),
                d["source_id"],
                row["revision"],
            ]
        )
        latex.append(
            f"{tex_escape(d['title'])} & {value} & {tex_escape(d['protocol']['unit'])} & "
            f"{tex_escape(d['state'])} " + r"\\"
        )
    latex.append(r"\end{tabular}")
    files = {
        "table.csv": store.save_blob(output.getvalue().encode()),
        "table.tex": store.save_blob("\n".join(latex).encode()),
        "inputs.json": store.save_blob(encoded(rows).encode()),
    }
    with store.db(write=True) as db:
        # Recheck versions after rendering: never associate old values with new revisions.
        for row in rows:
            if store.record(db, row["id"])["revision"] != row["revision"]:
                from .database import Conflict

                raise Conflict("An observation changed while generating the table. Try again.")
        artifact = store.put(
            db,
            "artifact",
            {
                "title": title,
                "type": "table",
                "files": files,
                "input_snapshots": rows,
                "comparisons": comparisons,
                "comparable": comparable,
                "ranking_applied": False,
            },
        )
        for identifier in identifiers:
            store.link(db, identifier, artifact["id"], "table_input")
        return artifact


PLOT_SCRIPT = """import json
from pathlib import Path
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
root=Path(__file__).parent
data=json.loads((root/'inputs.json').read_text())
normalization=json.loads((root/'normalization.json').read_text())
style=json.loads((root/'style.json').read_text())
fig=Figure(figsize=(7,4.2),layout='constrained');FigureCanvasAgg(fig)
ax=fig.subplots()
labels=[r['data']['title'] for r in data]
values=[r['data']['value']*normalization['factors'][i] for i,r in enumerate(data)]
ax.bar(range(len(values)),values,color=style['color'],width=.62)
ax.set_xticks(range(len(values)),labels,rotation=20,ha='right')
ax.set_ylabel(data[0]['data']['protocol']['metric']+' ('+data[0]['data']['protocol']['unit']+')')
ax.spines[['top','right']].set_visible(False)
ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
for ext in ['pdf','svg','png']:fig.savefig(root/('figure.'+ext),dpi=200)
"""


def make_figure(store, title, identifiers):
    import subprocess
    import sys
    import tempfile

    if not 2 <= len(identifiers) <= 30:
        raise ValueError("Choose 2–30 observations for a comparison figure.")
    factors = [1.0]
    comparisons = []
    for identifier in identifiers[1:]:
        comparison = compare_observations(store, identifiers[0], identifier)
        if comparison["status"] != "comparable":
            raise ValueError(
                "Plotting comparative values requires verified matching evaluation conditions."
            )
        factors.append(
            next(
                (v["right_to_left"] for v in comparison["conversions"] if v["field"] == "unit"), 1.0
            )
        )
        comparisons.append(comparison)
    rows = [store.get(i, "observation") for i in identifiers]
    with tempfile.TemporaryDirectory(prefix="deeptutor-figure-") as tmp:
        root = Path(tmp)
        (root / "inputs.json").write_text(encoded(rows))
        (root / "normalization.json").write_text(
            encoded(
                {
                    "unit": rows[0]["data"]["protocol"]["unit"],
                    "factors": factors,
                    "comparisons": comparisons,
                }
            )
        )
        (root / "style.json").write_text(encoded({"color": "#477965", "version": 1}))
        (root / "plot.py").write_text(PLOT_SCRIPT)
        # This is our fixed, versioned script. No uploaded code is executed.
        result = subprocess.run(
            [sys.executable, str(root / "plot.py")], cwd=root, capture_output=True, timeout=45
        )
        if result.returncode:
            raise ValueError("Plot generation failed; no output was published.")
        files = {p.name: store.save_blob(p.read_bytes()) for p in root.iterdir() if p.is_file()}
    with store.db(write=True) as db:
        for row in rows:
            if store.record(db, row["id"])["revision"] != row["revision"]:
                from .database import Conflict

                raise Conflict("Inputs changed while plotting. Regenerate using the latest values.")
        record = store.put(
            db,
            "artifact",
            {
                "title": title,
                "type": "figure",
                "files": files,
                "input_snapshots": rows,
                "script_version": 2,
            },
        )
        for identifier in identifiers:
            store.link(db, identifier, record["id"], "figure_input")
        return record


def import_run_rows(store, text, format):
    if len(text.encode()) > 5_000_000:
        raise ValueError("Run manifest exceeds 5 MB.")
    rows = list(csv.DictReader(io.StringIO(text))) if format == "csv" else json.loads(text)
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
        raise ValueError("Import 1–500 run records.")
    results = []
    occurrences = {}
    for n, row in enumerate(rows):
        try:
            if not isinstance(row, dict):
                raise ValueError("Each run must be an object.")
            row = dict(row)
            identity = row.pop("import_identity", {})
            if not isinstance(identity, dict) or set(identity) - {
                "connector",
                "external_run_id",
                "snapshot",
                "idempotency_key",
            }:
                raise ValueError("Invalid import identity.")
            if isinstance(row.get("configuration"), str):
                row["configuration"] = json.loads(row["configuration"] or "{}")
            if isinstance(row.get("seed"), str):
                row["seed"] = int(row["seed"]) if row["seed"] else None
            command = RunInput(**row)
            payload_hash = sha(encoded(command.model_dump()).encode())
            occurrences[payload_hash] = occurrences.get(payload_hash, 0) + 1
            if identity:
                if not all(isinstance(v, str) and 0 < len(v) <= 500 for v in identity.values()):
                    raise ValueError("Import identity fields must be nonempty strings.")
                if not identity.get("idempotency_key") and not all(
                    identity.get(k) for k in ("connector", "external_run_id", "snapshot")
                ):
                    raise ValueError(
                        "Provide an operation key or connector, external run ID and snapshot."
                    )
                key = sha(encoded(identity).encode())
            else:
                key = sha(encoded(["manifest", payload_hash, occurrences[payload_hash]]).encode())
            record = create_run(store, command, import_identity=(key, payload_hash))
            results.append({"index": n, "status": "imported", "id": record["id"]})
        except (ValueError, TypeError) as exc:
            results.append({"index": n, "status": "error", "detail": str(exc)})
    return results
