"""Draw the benchmark results as three figures.

    chart_documents.png   what happened to every cell of the documents a model
                          read, one stacked bar per approach, with the spread
                          between runs marked where more than one run is on disk
    chart_tabular.png     how much of each spreadsheet reached the table, per
                          file, one line per approach
    chart_cost.png        tokens spent per document by each approach

    uv run python bench/run.py chart

Reads `results/raw/` and any `results/raw_*/` sibling and writes PNG and SVG
into `results/`.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
CONDITIONS = ["whole_schema", "whole_schema_sql", "one_table_text", "schemagate"]
LABELS = {
    "whole_schema": "whole schema\n+ document\nJSON asked for",
    "whole_schema_sql": "whole schema\n+ document\nSQL asked for",
    "one_table_text": "one table\n+ text layer\nfree JSON",
    "schemagate": "SchemaGate",
}
SHORT = {
    "whole_schema": "whole schema, JSON",
    "whole_schema_sql": "whole schema, SQL",
    "one_table_text": "one table, free JSON",
    "schemagate": "SchemaGate",
}
OUTCOMES = [
    ("correct", "correct", "#2b6a86"),
    ("flagged", "flagged or held for review", "#d9a441"),
    ("wrong_silent", "wrong value stored", "#9b3b2e"),
    ("wrong_null", "left blank", "#b8c4cc"),
    ("rejected", "rejected by the database", "#4a5661"),
    ("missing", "row never came back", "#e6e9e7"),
]
PALETTE = {
    "whole_schema": "#9aa5ad",
    "whole_schema_sql": "#6b7680",
    "one_table_text": "#3b4650",
    "schemagate": "#2b6a86",
}
INK = "#172029"
MUTED = "#6b7680"
STYLE = {
    "font.family": ["Segoe UI", "DejaVu Sans"],
    "font.size": 10,
    "axes.edgecolor": MUTED,
    "axes.labelcolor": INK,
    "xtick.color": INK,
    "ytick.color": INK,
    "text.color": INK,
    "figure.facecolor": "white",
}


def load_runs() -> dict[str, list[dict[str, Any]]]:
    runs: dict[str, list[dict[str, Any]]] = {}
    for directory in sorted(RESULTS.glob("raw*")):
        if not directory.is_dir():
            continue
        scores = [
            json.loads(path.read_text(encoding="utf-8"))["score"]
            for path in directory.glob("*/*/*.json")
        ]
        if scores:
            runs[directory.name] = scores
    return runs


def document_shares(scores: list[dict[str, Any]], model: str) -> dict[str, dict[str, float]]:
    """Share of cells in each outcome per approach, for documents a model read."""
    shares: dict[str, dict[str, float]] = {}
    for condition in CONDITIONS:
        group = [
            s
            for s in scores
            if s["model"] == model
            and s["condition"] == condition
            and s["kind"] != "tabular"
            and not str(s["error"]).startswith("unsupported")
        ]
        cells = sum(s["cells"] for s in group) or 1
        shares[condition] = {
            "correct": sum(s["correct"] for s in group) / cells,
            "flagged": sum(s["wrong_flagged"] + s.get("held", 0) for s in group) / cells,
            "wrong_silent": sum(s["wrong_silent"] for s in group) / cells,
            "wrong_null": sum(s.get("wrong_null", 0) for s in group) / cells,
            "rejected": sum(s["rejected"] for s in group) / cells,
            "missing": sum(s["missing"] for s in group) / cells,
            "tokens": statistics.mean(s["input_tokens"] + s["output_tokens"] for s in group)
            if group
            else 0,
            "ms": statistics.median(s["ms"] for s in group) if group else 0,
        }
    return shares


def case_notes() -> dict[str, str]:
    manifest = ROOT / "data" / "manifest.jsonl"
    notes: dict[str, str] = {}
    if manifest.exists():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                notes[entry["id"]] = entry.get("notes", "")
    return notes


def tabular_label(score: dict[str, Any], notes: dict[str, str]) -> str:
    parts = [part.strip() for part in notes.get(score["case"], "").split(",")]
    language = parts[0].replace(" headings", "") if parts and parts[0] else ""
    fmt = parts[2] if len(parts) > 2 else ""
    second = " ".join(part for part in (language, fmt) if part)
    label = f"{score['rows_expected']} rows"
    return label + "\n" + second if second else label


def draw_documents(
    shares_by_run: dict[str, dict[str, dict[str, float]]], latest: str, model: str, output: Path
) -> None:
    shares = shares_by_run[latest]
    fig, ax = plt.subplots(figsize=(9, 5.8))
    x = list(range(len(CONDITIONS)))
    bottoms = [0.0] * len(CONDITIONS)
    for key, _, colour in OUTCOMES:
        values = [shares[c][key] * 100 for c in CONDITIONS]
        ax.bar(x, values, bottom=bottoms, color=colour, width=0.6, edgecolor="white", linewidth=0.6)
        bottoms = [b + v for b, v in zip(bottoms, values, strict=True)]
    for index, condition in enumerate(CONDITIONS):
        share = shares[condition]
        ax.text(index, share["correct"] * 50, f"{share['correct'] * 100:.0f}%", ha="center",
                va="center", color="white", fontsize=12, fontweight="bold")
        flagged = share["flagged"] * 100
        wrong = share["wrong_silent"] * 100
        flagged_y = (share["correct"] + share["flagged"] / 2) * 100
        wrong_y = (share["correct"] + share["flagged"] + share["wrong_silent"] / 2) * 100
        if flagged > 0.3:
            ax.text(index + 0.34, flagged_y, f"{flagged:.1f}% flagged", ha="left", va="center",
                    fontsize=8.5, color="#a6690f")
        if wrong > 0.3:
            # Keep the two labels apart when both slices are thin.
            wrong_y = max(wrong_y, flagged_y + 3) if flagged > 0.3 else wrong_y
            ax.text(index + 0.34, wrong_y, f"{wrong:.1f}% wrong", ha="left", va="center",
                    fontsize=8.5, color="#9b3b2e")
    if len(shares_by_run) > 1:
        for index, condition in enumerate(CONDITIONS):
            corrects = [r[condition]["correct"] * 100 for r in shares_by_run.values()]
            low, high = min(corrects), max(corrects)
            ax.plot([index - 0.3, index - 0.3], [low, high], color=INK, linewidth=1.6)
            ax.plot([index - 0.36, index - 0.24], [low, low], color=INK, linewidth=1.6)
            ax.plot([index - 0.36, index - 0.24], [high, high], color=INK, linewidth=1.6)
    ax.set_xticks(x)
    ax.set_xticklabels([LABELS[c] for c in CONDITIONS], fontsize=9.5)
    ax.set_xlim(-0.6, len(CONDITIONS) - 0.2)
    ax.set_ylim(0, 100)
    ax.set_ylabel("share of expected cells, %")
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title(
        f"PDF invoices, statements and line items: what happened to each value\n{model}, "
        f"same model in every approach",
        loc="left",
        fontsize=11.5,
    )
    ax.legend(handles=[Patch(color=c, label=l) for _, l, c in OUTCOMES], loc="upper center",
              bbox_to_anchor=(0.5, -0.22), ncol=3, frameon=False, fontsize=9)
    if len(shares_by_run) > 1:
        ax.text(0.5, -0.36,
                f"Bars show the latest run. The mark beside each bar is the spread of the "
                f"correct share over {len(shares_by_run)} runs.",
                transform=ax.transAxes, ha="center", va="top", fontsize=8.5, color=MUTED)
    fig.tight_layout()
    fig.savefig(output, dpi=160)
    fig.savefig(output.with_suffix(".svg"))
    plt.close(fig)


def draw_tabular(
    runs: dict[str, list[dict[str, Any]]], latest: str, model: str, output: Path
) -> None:
    scores = runs[latest]
    notes = case_notes()
    cases = sorted({s["case"] for s in scores if s["kind"] == "tabular"})
    by_case = {
        (s["condition"], s["case"]): s
        for s in scores
        if s["model"] == model and s["kind"] == "tabular"
    }
    labels = [
        tabular_label(by_case[("schemagate", case)], notes)
        for case in cases
        if ("schemagate", case) in by_case
    ]
    fig, ax = plt.subplots(figsize=(9, 5.4))
    positions = list(range(len(cases)))

    def values_for(run_scores: list[dict[str, Any]], condition: str) -> list[float]:
        found = {
            s["case"]: s
            for s in run_scores
            if s["model"] == model and s["kind"] == "tabular" and s["condition"] == condition
        }
        return [
            100 * found[case]["correct"] / found[case]["cells"]
            if case in found and found[case]["cells"]
            else 0
            for case in cases
        ]

    for condition in CONDITIONS:
        per_run = [values_for(run_scores, condition) for run_scores in runs.values()]
        lows = [min(v[i] for v in per_run) for i in positions]
        highs = [max(v[i] for v in per_run) for i in positions]
        means = [sum(v[i] for v in per_run) / len(per_run) for i in positions]
        if len(per_run) > 1:
            ax.fill_between(positions, lows, highs, color=PALETTE[condition], alpha=0.18, linewidth=0)
        ax.plot(positions, means, marker="o", markersize=6, color=PALETTE[condition],
                linewidth=2.6 if condition == "schemagate" else 1.5, label=SHORT[condition])
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(-4, 104)
    ax.set_ylabel("cells that reached the table correctly, %")
    ax.spines[["top", "right"]].set_visible(False)
    runs_note = f", mean of {len(runs)} runs, spread shaded" if len(runs) > 1 else ""
    title = f"Spreadsheets and CSV files, one point per file{runs_note}"
    ax.set_title(title + "\n" + model, loc="left", fontsize=11.5)
    ax.legend(frameon=False, fontsize=9, loc="center left", bbox_to_anchor=(0.02, 0.45))
    ax.text(0.0, -0.22,
            "SchemaGate copies the rows by code and asks the model about the headings once per "
            "heading set, so its line is flat by design.\nThe other approaches push every row "
            "through the model in one call: on small files the model sometimes stops early,\n"
            "above 500 rows its output is cut off or refused.",
            transform=ax.transAxes, ha="left", va="top", fontsize=8.5, color=MUTED)
    fig.tight_layout()
    fig.savefig(output, dpi=160)
    fig.savefig(output.with_suffix(".svg"))
    plt.close(fig)


def draw_cost(shares: dict[str, dict[str, float]], model: str, output: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.5, 5.2))
    tokens = [shares[c]["tokens"] for c in CONDITIONS]
    bars = ax.bar(range(len(CONDITIONS)), tokens, color=[PALETTE[c] for c in CONDITIONS], width=0.6)
    for bar, value, condition in zip(bars, tokens, CONDITIONS, strict=True):
        ax.text(bar.get_x() + bar.get_width() / 2, value,
                f"{value:,.0f} tokens\n{shares[condition]['ms']:.0f} ms",
                ha="center", va="bottom", fontsize=9)
    ax.set_xticks(range(len(CONDITIONS)))
    ax.set_xticklabels([LABELS[c] for c in CONDITIONS], fontsize=9.5)
    ax.set_ylim(0, max(tokens) * 1.2)
    ax.set_ylabel("mean tokens per document, in and out")
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title(f"What each document cost the model, with median latency\n{model}", loc="left",
                 fontsize=11.5)
    fig.tight_layout()
    fig.savefig(output, dpi=160)
    fig.savefig(output.with_suffix(".svg"))
    plt.close(fig)


def draw() -> list[Path]:
    runs = load_runs()
    if not runs:
        raise SystemExit("No results to draw.")
    latest = "raw" if "raw" in runs else sorted(runs)[-1]
    model = sorted({s["model"] for s in runs[latest]})[0]
    plt.rcParams.update(STYLE)
    shares_by_run = {name: document_shares(scores, model) for name, scores in runs.items()}
    outputs = [
        RESULTS / "chart_documents.png",
        RESULTS / "chart_tabular.png",
        RESULTS / "chart_cost.png",
    ]
    draw_documents(shares_by_run, latest, model, outputs[0])
    draw_tabular(runs, latest, model, outputs[1])
    draw_cost(shares_by_run[latest], model, outputs[2])
    return outputs


if __name__ == "__main__":
    for path in draw():
        print(path)
