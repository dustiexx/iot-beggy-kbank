"""Scoring for the posture evaluation (pure functions, unit-tested).

Each analysed frame has a ground-truth label (what the user was asked to do) and the
detector's raw output for that frame (issues found, or no person).
"""
from __future__ import annotations

from collections import Counter

LABELS = ["good", "slouch", "head_down", "too_close", "uneven_shoulders", "away"]
# when several issues fire at once, the frame is filed under the first one in this order
PRIORITY = ["too_close", "head_down", "slouch", "uneven_shoulders"]


def predicted_class(person: bool, raw_issues: list[str], truth: str | None = None) -> str:
    """Single class for the confusion matrix. If the asked-for issue is among the issues found,
    the frame counts as that issue (several issues at once is normal, e.g. slouch + head_down)."""
    if not person:
        return "away"
    if not raw_issues:
        return "good"
    if truth in raw_issues:
        return truth
    return next((i for i in PRIORITY if i in raw_issues), raw_issues[0])


def is_bad(cls: str) -> bool:
    return cls not in ("good", "away")


def score(rows: list[dict]) -> dict:
    """rows: {"truth": label, "pred": class}. Returns accuracy numbers + confusion matrix."""
    n = len(rows)
    if not n:
        return {"frames": 0}
    exact = sum(r["truth"] == r["pred"] for r in rows)
    # what actually matters for the alert: bad vs good
    posture_rows = [r for r in rows if r["truth"] != "away"]
    tp = sum(is_bad(r["truth"]) and is_bad(r["pred"]) for r in posture_rows)
    tn = sum(r["truth"] == "good" and r["pred"] == "good" for r in posture_rows)
    fp = sum(r["truth"] == "good" and is_bad(r["pred"]) for r in posture_rows)
    fn = sum(is_bad(r["truth"]) and not is_bad(r["pred"]) for r in posture_rows)
    labels = [l for l in LABELS if any(r["truth"] == l or r["pred"] == l for r in rows)]
    matrix = {t: {p: 0 for p in labels} for t in labels}
    for r in rows:
        matrix[r["truth"]][r["pred"]] += 1
    per_class = {}
    for l in labels:
        support = sum(r["truth"] == l for r in rows)
        hit = matrix[l][l]
        predicted = sum(matrix[t][l] for t in labels)
        per_class[l] = {
            "frames": support,
            "recall": round(hit / support, 3) if support else None,
            "precision": round(hit / predicted, 3) if predicted else None,
        }
    return {
        "frames": n,
        "accuracy": round(exact / n, 3),
        "good_vs_bad": {
            "accuracy": round((tp + tn) / len(posture_rows), 3) if posture_rows else None,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "false_alarm_rate": round(fp / (fp + tn), 3) if fp + tn else None,
            "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        },
        "per_class": per_class,
        "labels": labels,
        "confusion": matrix,
        "truth_counts": dict(Counter(r["truth"] for r in rows)),
    }


def markdown_report(s: dict, perf: dict, meta: dict) -> str:
    lines = [f"# Posture evaluation - {meta.get('date', '')}", "",
             f"Camera: {meta.get('source')}  |  rounds: {meta.get('rounds')}  |  "
             f"seconds per pose: {meta.get('hold_s')}  |  frames scored: {s['frames']}", "",
             "## Speed", "",
             "| metric | value |", "|---|---|",
             f"| pose model inference (mean / p95) | {perf['infer_ms_mean']} ms / {perf['infer_ms_p95']} ms |",
             f"| model-only throughput | {perf['model_fps']} fps |",
             f"| end-to-end pipeline (camera + model + features) | {perf['pipeline_fps']} fps |", "",
             "## Accuracy", "",
             f"- **Overall (exact class): {s['accuracy']:.1%}**"]
    g = s["good_vs_bad"]
    if g["accuracy"] is not None:
        lines += [f"- **Good vs bad posture (what drives the alert): {g['accuracy']:.1%}**  "
                  f"(precision {g['precision']}, recall {g['recall']}, false alarms {g['false_alarm_rate']})"]
    lines += ["", "| pose | frames | recall | precision |", "|---|---|---|---|"]
    for l, v in s["per_class"].items():
        lines.append(f"| {l} | {v['frames']} | {v['recall']} | {v['precision']} |")
    labels = s["labels"]
    lines += ["", "## Confusion matrix (rows = asked pose, columns = detected)", "",
              "| asked \\ detected | " + " | ".join(labels) + " |",
              "|---|" + "---|" * len(labels)]
    for t in labels:
        lines.append(f"| **{t}** | " + " | ".join(str(s["confusion"][t][p]) for p in labels) + " |")
    lines += ["", "Example frames: `correct/` and `wrong/` next to this file "
              "(file name = asked pose _ detected class)."]
    return "\n".join(lines) + "\n"
