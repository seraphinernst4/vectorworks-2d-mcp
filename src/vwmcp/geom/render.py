"""Check image ("Prüfbild"): the drawing as Vectorworks stores it, with
issues marked. Rendered from the exact geometry, not a screenshot."""

from __future__ import annotations

import io
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import LineCollection, PatchCollection  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402

from vwmcp.geom import curves as C  # noqa: E402
from vwmcp.geom.shapes import Shape  # noqa: E402

SEV_COLOR = {"error": "#d7263d", "warning": "#f08c00", "info": "#1c7ed6"}


def _bounds(shapes, records, issues, focus):
    if focus:
        return focus
    xs, ys = [], []
    for r in records:
        b = r.get("bbox")
        if b and r["kind"] != "group":
            xs += [b[0], b[2]]
            ys += [b[1], b[3]]
    for it in issues:
        if it.get("at"):
            xs.append(it["at"][0])
            ys.append(it["at"][1])
    if not xs:
        return (0, 0, 1, 1)
    return (min(xs), min(ys), max(xs), max(ys))


def render(records: list[dict], shapes: list[Shape], issues: list[dict], chord: float,
           focus=None, width_px: int = 1600, title: str = "", show_ids: bool = True,
           mpu: float = 1.0) -> bytes:
    x0, y0, x1, y1 = _bounds(shapes, records, issues, focus)
    w, h = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    pad = 0.04 * max(w, h)
    x0, x1, y0, y1 = x0 - pad, x1 + pad, y0 - pad, y1 + pad
    w, h = x1 - x0, y1 - y0
    aspect = h / w
    fig_w = width_px / 100.0
    fig_h = max(3.0, min(fig_w * aspect, fig_w * 1.6))
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=100)
    ax.set_aspect("equal")
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_facecolor("white")
    # sampling fine enough for the view, not finer than the check precision
    view_chord = max(chord, max(w, h) / 4000.0)

    polys, faces, edges = [], [], []
    lines, lcolors = [], []
    for s in shapes:
        if not s.rings:
            continue
        a = s.attrs
        pen = a.get("pen", "#000000")
        if s.is_area:
            for i, ring in enumerate(s.rings):
                pts = C.discretize(ring, view_chord)
                if len(pts) < 3:
                    continue
                fill = a.get("fill", "solid")
                face = "none"
                if fill == "solid":
                    face = a.get("fill_color", "#ffffff") if i == 0 else "#ffffff"
                elif fill in ("hatch", "pattern"):
                    face = "#d9d9d9" if i == 0 else "#ffffff"
                polys.append(MplPolygon(pts, closed=True))
                faces.append(face)
                edges.append(pen)
        else:
            pts = C.discretize(s.segments, view_chord)
            lines.append(pts)
            lcolors.append(pen)
    if polys:
        pc = PatchCollection(polys, facecolors=faces, edgecolors=edges, linewidths=0.6, alpha=0.55)
        ax.add_collection(pc)
    if lines:
        ax.add_collection(LineCollection(lines, colors=lcolors, linewidths=0.8))
    for r in records:
        if r["kind"] == "text" and r.get("origin"):
            ax.plot(*r["origin"], marker="o", ms=2, color="#555555")
            ax.text(r["origin"][0], r["origin"][1], r.get("text", "")[:40], fontsize=6, color="#555555")
        elif r["kind"] == "dimension" and r.get("p1"):
            ax.plot([r["p1"][0], r["p2"][0]], [r["p1"][1], r["p2"][1]], ls=":", lw=0.6, color="#888888")

    # issues on top
    for it in issues:
        col = SEV_COLOR.get(it["severity"], "#d7263d")
        for reg in it.get("region") or []:
            ax.add_patch(MplPolygon(reg, closed=True, facecolor=col, edgecolor=col, alpha=0.7, lw=1.2))
        at = it.get("at")
        if at is None:
            continue
        ax.plot(at[0], at[1], marker="o", ms=14, mfc="none", mec=col, mew=2.0)
        for m in it.get("moves") or []:
            ax.annotate("", xy=m["target"], xytext=m["point"],
                        arrowprops=dict(arrowstyle="->", color=col, lw=1.2))
        if show_ids:
            ax.text(at[0], at[1], " %d" % it["id"], color=col, fontsize=9, fontweight="bold",
                    va="bottom", ha="left")
    _scale_bar(ax, x0, y0, w, h, mpu)
    n_err = sum(1 for i in issues if i["severity"] == "error")
    n_warn = sum(1 for i in issues if i["severity"] == "warning")
    ax.set_title((title + "  " if title else "") + "Fehler: %d   Warnungen: %d" % (n_err, n_warn),
                 fontsize=11, loc="left")
    ax.tick_params(labelsize=7)
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def _scale_bar(ax, x0, y0, w, h, mpu):
    """Scale bar with a round length in mm/m."""
    target = w / 5.0 * mpu  # mm
    exp = 10 ** math.floor(math.log10(target)) if target > 0 else 1
    for f in (1, 2, 5, 10):
        if f * exp >= target * 0.6:
            L_mm = f * exp
            break
    L = L_mm / mpu
    bx, by = x0 + 0.03 * w, y0 + 0.04 * h
    ax.plot([bx, bx + L], [by, by], color="black", lw=2)
    label = ("%g m" % (L_mm / 1000.0)) if L_mm >= 1000 else ("%g mm" % L_mm)
    ax.text(bx + L / 2, by + 0.01 * h, label, ha="center", va="bottom", fontsize=8)


def focus_box(issue: dict, size: float):
    at = issue.get("at")
    if not at:
        return None
    return (at[0] - size, at[1] - size, at[0] + size, at[1] + size)
