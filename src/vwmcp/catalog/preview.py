"""Preview images of assets (server side, matplotlib) - to check shapes
before drawing and to show Claude/the user what an asset looks like."""

from __future__ import annotations

import io
import math

ROLE_STYLE = {"outline": dict(lw=1.4, color="#303030"), "detail": dict(lw=0.7, color="#808080")}


def _arc_pts(cx, cy, r, a0, sweep, n=None):
    n = n or max(8, int(abs(sweep) / 4))
    return [(cx + r * math.cos(math.radians(a0 + sweep * i / n)),
             cy + r * math.sin(math.radians(a0 + sweep * i / n))) for i in range(n + 1)]


def item_points(it: dict) -> tuple[list[tuple[float, float]], bool]:
    """Polyline of an item (arcs sampled) and whether it is closed."""
    t = it["type"]
    if t == "rect":
        (x1, y1), (x2, y2) = it["p1"], it["p2"]
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)], True
    if t == "line":
        return [tuple(it["p1"]), tuple(it["p2"])], False
    if t == "circle":
        c = it["center"]
        return _arc_pts(c[0], c[1], it["radius"], 0, 360)[:-1], True
    if t == "arc":
        c = it["center"]
        return _arc_pts(c[0], c[1], it["radius"], it["start_angle"], it["sweep"]), False
    if t in ("polygon", "polyline"):
        return [tuple(p) for p in it["points"]], t == "polygon"
    if t == "path":
        pts = [tuple(it["start"])]
        for s in it["segments"]:
            if "line_to" in s:
                pts.append(tuple(s["line_to"]))
            else:
                if "center" in s:
                    center = s["center"]
                else:
                    from vwmcp.geom.specs import _center_from_radius
                    center = _center_from_radius(pts[-1], tuple(s["arc_to"]), s["radius"],
                                                 s.get("direction", "ccw") == "ccw", bool(s.get("large")))
                (x0, y0), (x1, y1), (cx, cy) = pts[-1], s["arc_to"], center
                a0 = math.degrees(math.atan2(y0 - cy, x0 - cx))
                a1 = math.degrees(math.atan2(y1 - cy, x1 - cx))
                sweep = (a1 - a0) % 360 if s.get("direction", "ccw") == "ccw" else -((a0 - a1) % 360)
                if abs(sweep) < 1e-9:
                    sweep = 360 if s.get("direction", "ccw") == "ccw" else -360
                pts.extend(_arc_pts(cx, cy, math.hypot(x0 - cx, y0 - cy), a0, sweep)[1:])
        return pts, it.get("closed", True)
    raise ValueError("unknown item type %r" % t)


def render(groups: list[tuple[str, list[dict]]], cols: int = 3, size: float = 3.2) -> bytes:
    """PNG with one panel per (title, items)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = max(1, math.ceil(len(groups) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(cols * size, rows * size), squeeze=False)
    for ax in axes.flat:
        ax.axis("off")
    for ax, (title, items) in zip(axes.flat, groups):
        for it in items:
            pts, closed = item_points(it)
            st = ROLE_STYLE.get(it.get("role", "outline"), ROLE_STYLE["outline"])
            xs = [p[0] for p in pts] + ([pts[0][0]] if closed else [])
            ys = [p[1] for p in pts] + ([pts[0][1]] if closed else [])
            if closed and it.get("role", "outline") == "outline":
                ax.fill(xs, ys, color="white", zorder=1)
            ax.plot(xs, ys, zorder=2, **st)
        ax.plot([0], [0], marker="+", color="#d04040", ms=8, zorder=3)
        ax.set_aspect("equal")
        ax.set_title(title, fontsize=8)
        ax.margins(0.08)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return buf.getvalue()
