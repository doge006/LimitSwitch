"""Where popups go on screen. Pure maths (unit-tested), no Windows calls.

Rule: the whole window, including its transparent shadow margin, stays inside the work
area. Windows treats every non-transparent pixel of a layered window (the soft shadow
too) as clickable, so any overlap with the taskbar would swallow clicks on the tray icon.
"""
from . import flyout_render as fr


def taskbar_edge(monitor, work):
    if work.left > monitor.left:
        return "left"
    if work.top > monitor.top:
        return "top"
    if work.right < monitor.right:
        return "right"
    return "bottom"


SLIDE = {"bottom": (0, 1), "top": (0, -1), "left": (-1, 0), "right": (1, 0)}


def clamp(value, low, high):
    return max(low, min(value, high))


def place_above(anchor, monitor, work, width, height, scale):
    """Top-left for the panel image: centred on the anchor (the tray icon), against the
    taskbar edge, entirely inside the work area. Returns (x, y, slide direction)."""
    ax, ay = anchor
    edge = taskbar_edge(monitor, work)
    min_x, max_x = work.left, work.right - width
    min_y, max_y = work.top, work.bottom - height
    if edge in ("bottom", "top"):
        x = clamp(round(ax - width / 2), min_x, max_x)
        y = max_y if edge == "bottom" else min_y
    else:
        y = clamp(round(ay - height / 2), min_y, max_y)
        x = min_x if edge == "left" else max_x
    return x, y, SLIDE[edge]


def place_menu(point, monitor, work, width, height, scale):
    """Context menu next to the pointer, never covering the point that was clicked."""
    px, py = point
    x = px - width if px + width > work.right else px
    y = py - height if py + height > work.bottom else py
    x = clamp(x, work.left, work.right - width)
    y = clamp(y, work.top, work.bottom - height)
    return x, y, SLIDE[taskbar_edge(monitor, work)]


def panel_contains(x, y, width, height):
    """Is a logical point (incl. margin) on the visible panel rather than its shadow?"""
    m = fr.MARGIN
    return m <= x < width - m and m <= y < height - m
