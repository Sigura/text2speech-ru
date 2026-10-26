"""`report.md`: the human-readable side of `report.json` (FR-17).

Written next to the audio after every `synthesize` run: what was produced, how long it
took, and what needs a listen — flagged segments with what the ASR heard.
"""

from __future__ import annotations

from typing import Any

MAX_ROWS = 50  # flagged segments, warnings, uncertain spans listed in full in report.json


def _hms(ms: int | float) -> str:
    s = round(ms / 1000)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _more(rows: list[Any]) -> list[str]:
    extra = len(rows) - MAX_ROWS
    return ["", f"… and {extra} more in `report.json`."] if extra > 0 else []


def _polish_line(polish: dict[str, Any] | None) -> str:
    if not polish:
        return ""
    line = f", then `{polish['backend']}` on {polish['segments']} segment(s)"
    if polish.get("workers"):
        line += f" ({polish['workers']} worker(s) on {polish['device']}, RTF {polish['rtf']})"
    return line


def render(report: dict[str, Any]) -> str:
    seg = report["segments"]
    syn = report["synthesis"]
    qa = report.get("qa", {})
    out = [
        f"# {report['book']}",
        "",
        f"- Audio: **{_hms(report['duration_ms'])}** in {len(report['files'])} file(s)"
        f" ({report['split']['mode']}: {report['split']['reason']})",
        f"- Language `{report['language']}`, tier `{report['tier']}`,"
        f" backend `{report['route']['backend']}`" + _polish_line(report.get("polish")),
        f"- Segments: {seg['total']} — {seg['rendered_now']} rendered now,"
        f" {seg['cached']} cached, {seg['duplicate']} duplicate, {seg['failed']} failed",
        f"- Run time: {_hms(report['wall_s'] * 1000)}"
        + (f", RTF {report['rtf_wall']} (wall)" if report.get("rtf_wall") is not None else "")
        + (f", {syn['rtf']} per worker" if syn.get("rtf") is not None else ""),
        f"- text2audio {report['text2audio']}",
    ]
    if report.get("parts"):
        out += ["", "## Files", "", "| # | File | Title | Duration |", "|---|---|---|---|"]
        out += [
            f"| {n} | {_cell(p['file'])} | {_cell(p['title'])} | {_hms(p['duration_ms'])} |"
            for n, p in enumerate(report["parts"], 1)
        ]
    out += ["", "## Quality check", ""]
    if not qa.get("checked"):
        out.append(f"QA mode `{qa.get('mode', 'off')}`: no segments checked.")
    else:
        out.append(
            f"{qa['checked']} segments checked with `{qa['asr']}` (mode `{qa['mode']}`,"
            f" fails at WER > {qa['threshold']} and CER > {qa['cer_threshold']}):"
            f" mean WER {qa['mean_wer']},"
            f" {qa['retried']} retried, **{qa['flagged']} flagged"
            f" ({qa['flagged_share']:.1%})**."
            + (
                f" {qa['unpolished']} kept unpolished (§6.6.6 fallback)."
                if qa.get("unpolished")
                else ""
            )
        )
        flagged = qa.get("flagged_list", [])
        if flagged:
            out += [
                "",
                "| Segment | WER | CER | Flags | Text | Heard |",
                "|---|---|---|---|---|---|",
            ]
            out += [
                f"| {f['segment']} | {f['wer']} | {f['cer']} | {', '.join(f['flags'])} |"
                f" {_cell(f['text_norm'])} | {_cell(f['heard'])} |"
                for f in flagged[:MAX_ROWS]
            ]
            out += _more(flagged)
        unverified = qa.get("unverified", [])
        if unverified:
            out += ["", "Words read from Latin script that the ASR did not write (not scored):", ""]
            out += [
                f"- {u['segment']}: {_cell(' '.join(u['words']))}" for u in unverified[:MAX_ROWS]
            ]
            out += _more(unverified)
    timings = report.get("timings_s", {})
    if timings:
        out += ["", "## Time per stage", "", "| Stage | Time |", "|---|---|"]
        out += [f"| {k} | {_hms(v * 1000)} |" for k, v in timings.items()]
    uncertain = report.get("normalization", {}).get("uncertain", [])
    if uncertain:
        out += ["", "## Uncertain normalization", ""]
        out += [f"- {u['segment']}: `{_cell(u['span'])}`" for u in uncertain[:MAX_ROWS]]
        out += _more(uncertain)
    spans = report.get("foreign_spans", [])
    if spans:
        out += ["", "## Foreign-script spans", "", "| Segment | Span | Resolution | Reading |"]
        out += ["|---|---|---|---|"]
        out += [
            f"| {s['segment']} | {_cell(s['text'])} | {s['resolution']}"
            f" | {_cell(s['reading'] or '')} |"
            for s in spans[:MAX_ROWS]
        ]
        out += _more(spans)
    warnings = report.get("warnings", []) + [
        {"segment": f["segment"], "warning": f["error"]} for f in seg.get("failures", [])
    ]
    if warnings:
        out += ["", "## Warnings", ""]
        out += [f"- {w['segment']}: {_cell(w['warning'])}" for w in warnings[:MAX_ROWS]]
        out += _more(warnings)
    return "\n".join(out) + "\n"
