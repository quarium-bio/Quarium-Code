"""Shared look-and-feel helpers for the finance screens."""

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


def rounded_rect(canvas, x0, y0, x1, y1, radius, **kwargs):
    """Canvas has no rounded rectangle; approximate one with a smoothed polygon."""
    radius = min(radius, (x1 - x0) / 2, (y1 - y0) / 2)
    points = [
        x0 + radius, y0, x1 - radius, y0, x1, y0, x1, y0 + radius,
        x1, y1 - radius, x1, y1, x1 - radius, y1, x0 + radius, y1,
        x0, y1, x0, y1 - radius, x0, y0 + radius, x0, y0,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


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
