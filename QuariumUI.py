"""Shared look-and-feel helpers for the finance screens."""

import datetime
import math
import re
import tkinter as tk
from tkinter import ttk

# Flat, low-chrome palette: one accent, soft neutrals, no bevels.
C_BG = "#FFFFFF"
C_SURFACE = "#F9FAFB"
C_BORDER = "#E8EAED"
C_TEXT = "#111827"
C_MUTED = "#6B7280"
C_FAINT = "#9CA3AF"
C_DONE = "#16A34A"
C_TODO = "#E5E7EB"
C_PRIMARY = "#285D80"
C_DANGER = "#B91C1C"

UI_FONT = "Segoe UI"


def format_br_currency(value):
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        value = 0.0
    return "R$ " + f"{value:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')


def apply_modern_style(widget):
    """Flattens ttk chrome and switches to the system UI font."""
    style = ttk.Style(widget)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", font=(UI_FONT, 10), background=C_BG, foreground=C_TEXT)
    style.configure("TFrame", background=C_BG)
    style.configure("Surface.TFrame", background=C_SURFACE)
    style.configure("TLabel", background=C_BG, foreground=C_TEXT)
    style.configure("Muted.TLabel", foreground=C_MUTED, font=(UI_FONT, 9))
    style.configure("Title.TLabel", font=(UI_FONT, 16), foreground=C_TEXT)
    style.configure("Section.TLabel", font=(UI_FONT, 11, "bold"), foreground=C_TEXT)
    style.configure("Caps.TLabel", font=(UI_FONT, 8, "bold"), foreground=C_FAINT)
    style.configure("Danger.TLabel", foreground=C_DANGER, font=(UI_FONT, 9))
    style.configure("Flat.TButton", font=(UI_FONT, 9), relief="flat", borderwidth=0,
                    padding=(12, 6), background=C_SURFACE, foreground=C_TEXT)
    style.map("Flat.TButton", background=[("active", "#EDEFF2")])
    style.configure("Tiny.TButton", font=(UI_FONT, 8), relief="flat", borderwidth=0,
                    padding=(6, 2), background=C_SURFACE, foreground=C_MUTED)
    style.map("Tiny.TButton", background=[("active", "#E3E7EB")])
    style.configure("Primary.TButton", font=(UI_FONT, 9), relief="flat", borderwidth=0,
                    padding=(14, 6), background=C_PRIMARY, foreground="#FFFFFF")
    style.map("Primary.TButton", background=[("active", "#20506F"),
                                             ("disabled", "#9FB3C2")])
    style.configure("TCombobox", font=(UI_FONT, 9))
    style.configure("Treeview", font=(UI_FONT, 10), rowheight=26, borderwidth=0,
                    fieldbackground=C_BG, background=C_BG)
    style.configure("Treeview.Heading", font=(UI_FONT, 9), relief="flat",
                    background=C_SURFACE, foreground=C_MUTED)
    style.layout("Flat.Vertical.TScrollbar", style.layout("Vertical.TScrollbar"))
    style.configure("Flat.Vertical.TScrollbar", background=C_SURFACE, troughcolor=C_BG,
                    borderwidth=0, arrowsize=12)
    style.configure("Split.Horizontal.TScale", background=C_BG, troughcolor=C_TODO)
    return style


MONTHS_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho",
             "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]

DATE_HINT = "DD/MM/AAAA  (dia primeiro, como 31/01/2026)"


def parse_br_date(text):
    """Reads a date written the Brazilian way: day first.

    31/01/2026, 31-01-2026 and 31.01.2026 all work, and so does the ISO
    2026-01-31, which is unambiguous in any locale. A two-digit year means
    20xx. Returns None when the text cannot be read as a date.

    Day-first matters: 03/05/2026 is 3 May here and 5 March in the United
    States, so callers should show describe_date() back to the user rather
    than let them guess which reading they got.
    """
    text = (text or "").strip()
    if not text:
        return None
    # ISO first: a four-digit leading group can only be a year.
    iso = re.match(r'^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$', text)
    if iso:
        year, month, day = (int(g) for g in iso.groups())
    else:
        parts = re.match(r'^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$', text)
        if not parts:
            return None
        day, month, year = (int(g) for g in parts.groups())
        if year < 100:
            year += 2000
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def describe_date(value):
    """Spells a date out, so which reading was taken is never in doubt."""
    if value is None:
        return ""
    return f"{value.day} de {MONTHS_PT[value.month - 1]} de {value.year}"


def format_br_date(value):
    return value.strftime("%d/%m/%Y") if value else ""


def rounded_rect(canvas, x0, y0, x1, y1, radius, **kwargs):
    """Canvas has no rounded rectangle, so trace one as a plain polygon.

    Two earlier attempts both leaked pixels at these sizes. A smoothed
    polygon treats its points as spline control points rather than points
    the curve passes through, so edges bow and corners spill. Corner
    pieslices are worse: Tk's arc rasteriser paints the corner of the arc's
    own bounding box, leaving a speck sitting diagonally off each corner.

    Walking the outline and handing Tk an ordinary unsmoothed polygon avoids
    both. Every point is on the boundary, and polygon filling does not
    overshoot it.
    """
    radius = max(0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    fill = kwargs.pop('fill', '')
    kwargs.pop('outline', None)

    if radius <= 0:
        # width=0: a stroke straddles the boundary, so half of it would land
        # outside the rectangle asked for.
        return canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline=fill,
                                       width=0, **kwargs)

    # Canvas y grows downwards, so angle 0 points right and 90 points down.
    steps = max(3, int(round(radius)))
    points = []
    for cx, cy, start in ((x1 - radius, y1 - radius, 0),      # bottom right
                          (x0 + radius, y1 - radius, 90),     # bottom left
                          (x0 + radius, y0 + radius, 180),    # top left
                          (x1 - radius, y0 + radius, 270)):   # top right
        for step in range(steps + 1):
            angle = math.radians(start + 90.0 * step / steps)
            points.extend((cx + radius * math.cos(angle),
                           cy + radius * math.sin(angle)))
    return canvas.create_polygon(points, smooth=False, fill=fill, outline=fill,
                                 width=0, **kwargs)


class ScrollableList(ttk.Frame):
    """Vertically scrolling container that only shows its scrollbar when the
    content actually overflows."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, background=C_BG, height=120)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                       style="Flat.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.inner.bind("<MouseWheel>", self._on_wheel)

    def _on_inner_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._sync_scrollbar()

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)
        self._sync_scrollbar()

    def _on_scroll(self, first, last):
        self.scrollbar.set(first, last)
        self._sync_scrollbar()

    def _sync_scrollbar(self):
        needed = self.inner.winfo_reqheight() > self.canvas.winfo_height()
        if needed and not self.scrollbar.winfo_ismapped():
            self.scrollbar.pack(side="right", fill="y")
        elif not needed and self.scrollbar.winfo_ismapped():
            self.scrollbar.pack_forget()

    def _on_wheel(self, event):
        if self.inner.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def clear(self):
        for child in self.inner.winfo_children():
            child.destroy()
