"""Preview images of planning layouts (server side, matplotlib), in the
active style's colours: room, movement areas, element symbols, doors and
the problems the check found (red)."""

from __future__ import annotations

import io
import math
import textwrap

from vwmcp.catalog.preview import item_points
from vwmcp.planning import engine as E


def _col(st: dict, role: str, key: str, default: str) -> str:
    c = (st.get("classes") or {}).get(role) or {}
    v = c.get(key)
    return v if isinstance(v, str) and v.startswith("#") else default


def element_outline(items: list[dict], p: E.Placed) -> list[tuple[list[tuple[float, float]], bool, str]]:
    """Symbol items (mm, local) placed in the room (cm)."""
    out = []
    for it in items:
        pts, closed = item_points(it)
        world = []
        for x, y in pts:
            rx, ry = E.rot(x / 10.0, y / 10.0, p.deg)
            world.append((p.ix + rx, p.iy + ry))
        out.append((world, closed, it.get("role", "outline")))
    return out


def drawn_areas(p: E.Placed, room: E.Rect | None = None) -> list[tuple[E.Rect, str]]:
    """Movement areas as drawn (Raumpilot): front areas from the wall to the
    required depth (role movement) plus the extra depth (movement_extra);
    clipped to the room when it is known."""
    if room is not None:
        return [(c, role) for r, role in drawn_areas(p) for c in [r.clip(room)] if c is not None]
    out = []
    s = p.spec
    for i, a in enumerate(s.areas):
        sh = p.shifts.get(i, 0.0)
        sd = p.sides.get(i)
        if a.side == "front" and p.wall:
            lx1, _, lx2, _ = a.local(s.w, s.d, None, sh)
            out.append((E.local_rect_to_world(lx1, -(s.d + a.d), lx2, 0.0, p.ix, p.iy, p.deg), "movement"))
            if a.d_extra and a.d_extra > a.d:
                out.append((E.local_rect_to_world(lx1, -(s.d + a.d_extra), lx2, -(s.d + a.d), p.ix, p.iy, p.deg),
                            "movement_extra"))
        else:  # free-standing elements and side areas: the area itself, extra depth lighter
            lx1, ly1, lx2, ly2 = a.local(s.w, s.d, None, sh, sd)
            out.append((E.local_rect_to_world(lx1, ly1, lx2, ly2, p.ix, p.iy, p.deg), "movement"))
            if a.d_extra and a.d_extra > a.d and a.side in ("front", "back"):
                ex1, ey1, ex2, ey2 = a.local(s.w, s.d, a.d_extra, sh, sd)
                if a.side == "front":
                    ey2 = ly1
                else:
                    ey1 = ly2
                out.append((E.local_rect_to_world(ex1, ey1, ex2, ey2, p.ix, p.iy, p.deg), "movement_extra"))
    return out


def render(panels: list[dict], st: dict, cols: int = 3, size: float = 3.4) -> bytes:
    """panels: [{"title", "layout": E.Layout, "symbols": [items per placed]}]."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Arc, Rectangle

    show = st.get("show") or {}
    mov = _col(st, "movement", "fill", "#b3d0df")
    mov_x = _col(st, "movement_extra", "fill", "#e6eff4")
    pen = _col(st, "object", "pen", "#3c3c3c")
    rows = max(1, math.ceil(len(panels) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(cols * size, rows * size), squeeze=False)
    for ax in axes.flat:
        ax.axis("off")
    for ax, panel in zip(axes.flat, panels):
        lay: E.Layout = panel["layout"]
        W, D = lay.W, lay.D
        if show.get("movement", True):
            parts = [(r, role) for p in lay.placed for r, role in drawn_areas(p, E.Rect(0, 0, W, D))]
            for r, role in sorted(parts, key=lambda x: x[1] != "movement_extra"):
                if role == "movement_extra" and not show.get("movement_extra", True):
                    continue
                ax.add_patch(Rectangle((r.x1, r.y1), r.w, r.d, color=mov_x if role == "movement_extra" else mov,
                                       lw=0, zorder=1))
        for p, items in zip(lay.placed, panel["symbols"]):
            for pts, closed, role in element_outline(items, p):
                xs = [q[0] for q in pts] + ([pts[0][0]] if closed else [])
                ys = [q[1] for q in pts] + ([pts[0][1]] if closed else [])
                if closed and role == "outline":
                    ax.fill(xs, ys, color="white", zorder=2)
                ax.plot(xs, ys, color=pen, lw=0.9 if role == "outline" else 0.5, zorder=3)
        bare = panel.get("bare")
        if not bare:
            ax.add_patch(Rectangle((0, 0), W, D, fill=False, ec="black", lw=2.0, zorder=4))
        for dr in lay.doors:
            sp = dr.span(W, D)
            if dr.wall in ("N", "S"):
                ax.plot([sp.x1, sp.x2], [sp.y1, sp.y1], color="white", lw=2.6, zorder=5)
            else:
                ax.plot([sp.x1, sp.x1], [sp.y1, sp.y2], color="white", lw=2.6, zorder=5)
            if dr.swing in ("in", "out"):
                inward = dr.swing == "in"
                # hinge corner and the leaf drawn open (90°)
                normal = {"N": (0, -1), "S": (0, 1), "W": (1, 0), "E": (-1, 0)}[dr.wall]
                if not inward:
                    normal = (-normal[0], -normal[1])
                ends = [(sp.x1, sp.y1), (sp.x2, sp.y2)]
                # left/right seen from inside: at_wall order (left corner first)
                left_first = {"N": True, "E": False, "S": False, "W": True}[dr.wall]
                hinge = ends[0] if (dr.hinge == "left") == left_first else ends[1]
                other = ends[1] if hinge == ends[0] else ends[0]
                leaf = (hinge[0] + normal[0] * dr.width, hinge[1] + normal[1] * dr.width)
                ax.plot([hinge[0], leaf[0]], [hinge[1], leaf[1]], color=pen, lw=0.9, zorder=5)
                a0 = math.degrees(math.atan2(other[1] - hinge[1], other[0] - hinge[0]))
                a1 = math.degrees(math.atan2(leaf[1] - hinge[1], leaf[0] - hinge[0]))
                lo, hi = sorted((a0, a1))
                if hi - lo > 180:
                    lo, hi = hi, lo + 360
                ax.add_patch(Arc(hinge, 2 * dr.width, 2 * dr.width, theta1=lo, theta2=hi, color=pen, lw=0.5,
                                 ls="--", zorder=5))
        for iss in lay.issues:
            if iss.where and iss.severity == "error":
                x1, y1, x2, y2 = iss.where
                ax.add_patch(Rectangle((x1, y1), max(x2 - x1, 1), max(y2 - y1, 1), fill=False, ec="#d02020",
                                       lw=1.2, zorder=6))
        if not bare:
            ax.text(W / 2, -0.06 * max(W, D), "%g" % W, ha="center", va="top", fontsize=7)
            ax.text(-0.06 * max(W, D), D / 2, "%g" % D, ha="right", va="center", fontsize=7, rotation=90)
        elif panel.get("caption"):
            ax.text(W / 2, -0.04 * max(W, D), textwrap.fill(panel["caption"], 52), ha="center", va="top",
                    fontsize=7)
        n_err = sum(1 for i in lay.issues if i.severity == "error")
        title = panel.get("title") or lay.name
        if len(title) > 48:
            title = title[:46] + "…"
        ax.set_title("%s%s" % (title, "" if not n_err else "  (%d Probleme)" % n_err), fontsize=8,
                     color="#b01010" if n_err else "black")
        ax.set_aspect("equal")
        m = 0.12 * max(W, D)
        ax.set_xlim(-m, W + m)
        ax.set_ylim(-m, D + m)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return buf.getvalue()
