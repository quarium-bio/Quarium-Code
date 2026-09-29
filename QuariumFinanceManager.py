import os
import sys
import math
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox
import tkinter.font as tkfont

import QuariumPayees as QP

# When frozen by PyInstaller, __file__ resolves inside the temporary
# extraction folder rather than the exe's real folder, so paths built from
# it point at a throwaway location. Use the exe's directory instead.
_BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))

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
ROW_HEIGHT = 34
BAR_WIDTH = 258
SEG_GAP = 3
COL_EST, COL_CLIENT, COL_TOTAL, COL_BAR = 16, 140, 330, 480
HEADER_H = 92
LABEL_ANGLE = 30


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
    style.configure("Amount.TLabel", font=(UI_FONT, 10))
    style.configure("Danger.TLabel", foreground=C_DANGER, font=(UI_FONT, 9))
    style.configure("Flat.TButton", font=(UI_FONT, 9), relief="flat", borderwidth=0,
                    padding=(12, 6), background=C_SURFACE, foreground=C_TEXT)
    style.map("Flat.TButton", background=[("active", "#EDEFF2")])
    style.configure("Primary.TButton", font=(UI_FONT, 9), relief="flat", borderwidth=0,
                    padding=(14, 6), background=C_PRIMARY, foreground="#FFFFFF")
    style.map("Primary.TButton", background=[("active", "#20506F")])
    style.configure("Treeview", font=(UI_FONT, 10), rowheight=26, borderwidth=0,
                    fieldbackground=C_BG, background=C_BG)
    style.configure("Treeview.Heading", font=(UI_FONT, 9), relief="flat",
                    background=C_SURFACE, foreground=C_MUTED)
    style.layout("Flat.Vertical.TScrollbar", style.layout("Vertical.TScrollbar"))
    style.configure("Flat.Vertical.TScrollbar", background=C_SURFACE, troughcolor=C_BG,
                    borderwidth=0, arrowsize=12)
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
        self.canvas = tk.Canvas(self, highlightthickness=0, background=C_BG)
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


class FinanceManager:
    def __init__(self, root, current_user="Unknown"):
        self.root = root
        self.current_user = current_user
        self.db_path = os.path.join(_BASE_DIR, 'projects.db')
        self.rows = []
        self._tooltip = None
        self._tooltip_key = None
        self._hover_row = None

        QP.init_all(current_user)
        apply_modern_style(root)
        self.create_ui()
        self.load_financial_data()

    # ------------------------------------------------------------------- UI

    def create_ui(self):
        main = ttk.Frame(self.root, padding=(20, 16))
        main.pack(fill="both", expand=True)

        header = ttk.Frame(main)
        header.pack(fill="x", pady=(0, 4))
        ttk.Label(header, text="Project Finances", style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="Refresh", command=self.load_financial_data,
                   style="Flat.TButton").pack(side="right")
        ttk.Label(main, text="Double-click a project to open its breakdown",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 12))

        # Stage names rise from each segment instead of a separate A-F legend.
        self.head_canvas = tk.Canvas(main, height=HEADER_H, highlightthickness=0, background=C_BG)
        self.head_canvas.pack(fill="x")
        self._draw_header()

        body = ttk.Frame(main)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, highlightthickness=0, background=C_BG, height=180)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview,
                               style="Flat.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda e: self._clear_hover())
        self.canvas.bind("<Double-1>", self._on_double_click)
        self.canvas.bind("<MouseWheel>",
                         lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

        ttk.Separator(main, orient="horizontal").pack(fill="x", pady=(12, 10))

        totals_head = ttk.Frame(main)
        totals_head.pack(fill="x")
        ttk.Label(totals_head, text="Overall Summary", style="Section.TLabel").pack(side="left")
        self.totals_frame = ttk.Frame(main)
        self.totals_frame.pack(fill="x", pady=(8, 0))
        self.totals_tree = ttk.Treeview(self.totals_frame, columns=("Amount",),
                                        show="tree headings", height=5)
        self.totals_tree.heading("#0", text="Category")
        self.totals_tree.heading("Amount", text="Total")
        self.totals_tree.column("#0", width=240)
        self.totals_tree.column("Amount", width=170, anchor="e")
        self.totals_tree.pack(fill="x")

    def _segment_bounds(self, index):
        span = (BAR_WIDTH + SEG_GAP) / len(QP.SEGMENTS)
        x0 = COL_BAR + index * span
        return x0, x0 + span - SEG_GAP

    def _draw_header(self):
        c = self.head_canvas
        c.delete("all")
        baseline = HEADER_H - 16
        for label, x in (("Estimate", COL_EST), ("Client", COL_CLIENT), ("Total Cost", COL_TOTAL)):
            c.create_text(x, baseline, text=label.upper(), anchor="w",
                          font=(UI_FONT, 8, "bold"), fill=C_FAINT)
        for index, (_column, _letter, label) in enumerate(QP.SEGMENTS):
            x0, x1 = self._segment_bounds(index)
            c.create_text((x0 + x1) / 2, baseline - 6, text=label, anchor="w",
                          angle=LABEL_ANGLE, font=(UI_FONT, 8), fill=C_MUTED)
        c.create_line(COL_EST, HEADER_H - 4, COL_BAR + BAR_WIDTH + 70, HEADER_H - 4, fill=C_BORDER)

    # ----------------------------------------------------------------- data

    def _segment_state(self, project_row, settled):
        return [bool(project_row.get(col)) if col else settled
                for col, _letter, _label in QP.SEGMENTS]

    def load_financial_data(self):
        self._clear_hover()
        self.canvas.delete("all")
        self.rows = []
        for item in self.totals_tree.get_children():
            self.totals_tree.delete(item)

        totals = {QP.COST_LABOR: 0.0, QP.COST_MAINTENANCE: 0.0,
                  QP.COST_REAGENTS: 0.0, QP.COST_PROFIT: 0.0}
        grand = 0.0

        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("ATTACH DATABASE ? AS clients_db", (os.path.join(_BASE_DIR, 'clients.db'),))
            cur.execute('''
                SELECT p.id, p.estimate_number, c.name, p.responsible_user,
                       COALESCE(p.data_sent_to_client, 0), COALESCE(p.data_approved_by_client, 0),
                       COALESCE(p.invoice_sent, 0), COALESCE(p.boleto_sent, 0),
                       COALESCE(p.invoice_paid, 0)
                FROM projects p
                LEFT JOIN clients_db.clients c ON p.client_id = c.id
                WHERE p.status > 0
                ORDER BY p.id DESC
            ''')
            projects = cur.fetchall()
            cur.execute("DETACH DATABASE clients_db")
            conn.close()
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not load financial data: {e}")
            return

        y = 4
        for (p_id, est_num, client, responsible, a_sent, b_appr, c_nf, d_bol, e_paid) in projects:
            lines, profit = QP.calculate_cost_lines(p_id)
            for line in lines:
                totals[line['cost_type']] = totals.get(line['cost_type'], 0.0) + line['amount']
            totals[QP.COST_PROFIT] += profit
            project_total = sum(l['amount'] for l in lines) + profit
            grand += project_total

            settled = QP.all_settled(p_id, responsible)
            state = self._segment_state({
                'data_sent_to_client': a_sent, 'data_approved_by_client': b_appr,
                'invoice_sent': c_nf, 'boleto_sent': d_bol, 'invoice_paid': e_paid,
            }, settled)
            self._draw_row(y, p_id, est_num, client or "Desconhecido", project_total,
                           state, responsible)
            y += ROW_HEIGHT

        self.canvas.configure(scrollregion=(0, 0, COL_BAR + BAR_WIDTH + 90, max(y, 10)))

        for label, key in (("Reagents", QP.COST_REAGENTS), ("Labor", QP.COST_LABOR),
                           ("Maintenance", QP.COST_MAINTENANCE), ("Profit", QP.COST_PROFIT)):
            self.totals_tree.insert("", "end", text=label, values=(format_br_currency(totals[key]),))
        self.totals_tree.insert("", "end", text="TOTAL", values=(format_br_currency(grand),))

    def _fit(self, text, max_px):
        """Truncates to the measured pixel width so a long client name cannot
        run into the next column."""
        text = text or ""
        font = getattr(self, '_row_font', None)
        if font is None:
            font = self._row_font = tkfont.Font(family=UI_FONT, size=10)
        if font.measure(text) <= max_px:
            return text
        budget = max_px - font.measure("...")
        clipped = ""
        for ch in text:
            if font.measure(clipped + ch) > budget:
                break
            clipped += ch
        return clipped.rstrip() + "..."

    def _draw_row(self, y, p_id, est_num, client, total, state, responsible):
        c = self.canvas
        band = c.create_rectangle(COL_EST - 8, y, COL_BAR + BAR_WIDTH + 78, y + ROW_HEIGHT - 4,
                                  fill=C_BG, outline="")
        mid = y + (ROW_HEIGHT - 4) / 2
        c.create_text(COL_EST, mid, text=self._fit(est_num, COL_CLIENT - COL_EST - 12),
                      anchor="w", font=(UI_FONT, 10), fill=C_TEXT)
        c.create_text(COL_CLIENT, mid, text=self._fit(client, COL_TOTAL - COL_CLIENT - 12),
                      anchor="w", font=(UI_FONT, 10), fill=C_TEXT)
        c.create_text(COL_TOTAL, mid, text=format_br_currency(total), anchor="w",
                      font=(UI_FONT, 10), fill=C_TEXT)

        segments = []
        for i, done in enumerate(state):
            x0, x1 = self._segment_bounds(i)
            rect = rounded_rect(c, x0, mid - 7, x1, mid + 7, 4,
                                fill=C_DONE if done else C_TODO, outline="")
            segments.append((x0, x1, i, rect))
        c.create_text(COL_BAR + BAR_WIDTH + 16, mid, text=f"{sum(state)}/{len(state)}",
                      anchor="w", font=(UI_FONT, 9), fill=C_FAINT)
        c.create_line(COL_EST - 8, y + ROW_HEIGHT - 4, COL_BAR + BAR_WIDTH + 78,
                      y + ROW_HEIGHT - 4, fill=C_BORDER)

        self.rows.append({'y0': y, 'y1': y + ROW_HEIGHT - 4, 'project_id': p_id,
                          'estimate': est_num, 'client': client, 'responsible': responsible,
                          'segments': segments, 'band': band})

    # -------------------------------------------------------------- pointer

    def _row_at(self, y):
        for row in self.rows:
            if row['y0'] <= y <= row['y1']:
                return row
        return None

    def _on_motion(self, event):
        y = self.canvas.canvasy(event.y)
        x = self.canvas.canvasx(event.x)
        row = self._row_at(y)

        if row is not self._hover_row:
            if self._hover_row is not None:
                self.canvas.itemconfig(self._hover_row['band'], fill=C_BG)
            if row is not None:
                self.canvas.itemconfig(row['band'], fill=C_SURFACE)
            self._hover_row = row

        if row:
            for x0, x1, index, _rect in row['segments']:
                if x0 <= x <= x1:
                    key = (row['project_id'], index)
                    if key != self._tooltip_key:
                        self._show_tooltip(event, QP.SEGMENTS[index][2])
                        self._tooltip_key = key
                    return
        self._hide_tooltip()

    def _clear_hover(self):
        if self._hover_row is not None:
            try:
                self.canvas.itemconfig(self._hover_row['band'], fill=C_BG)
            except tk.TclError:
                pass
            self._hover_row = None
        self._hide_tooltip()

    def _show_tooltip(self, event, text):
        self._hide_tooltip(keep_key=True)
        tip = tk.Toplevel(self.canvas)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{event.x_root + 14}+{event.y_root + 18}")
        tk.Label(tip, text=text, background="#1F2937", foreground="white",
                 font=(UI_FONT, 9), padx=10, pady=5, bd=0).pack()
        self._tooltip = tip

    def _hide_tooltip(self, keep_key=False):
        if self._tooltip is not None:
            try:
                self._tooltip.destroy()
            except tk.TclError:
                pass
            self._tooltip = None
        if not keep_key:
            self._tooltip_key = None

    def _on_double_click(self, event):
        row = self._row_at(self.canvas.canvasy(event.y))
        if not row:
            return
        self._clear_hover()
        dialog = ProjectFinanceDialog(self.root, row['project_id'], row['estimate'],
                                      row['client'], row['responsible'], self.current_user)
        self.root.wait_window(dialog)
        self.load_financial_data()

    def on_closing(self):
        self._clear_hover()


class ProjectFinanceDialog(tk.Toplevel):
    """Per-project finance detail: stage boxes, category totals, recipients."""

    def __init__(self, parent, project_id, estimate_number, client, responsible, current_user):
        super().__init__(parent)
        self.project_id = project_id
        self.estimate_number = estimate_number
        self.client = client
        self.responsible = responsible
        self.current_user = current_user
        self.segment_boxes = {}
        self.bubbles = {}

        self.title(f"{estimate_number}  -  Finance Breakdown")
        self.geometry("1000x580")
        self.configure(background=C_BG)
        self.transient(parent)
        self.grab_set()
        apply_modern_style(self)

        self._load_state()
        self._build_ui()
        self._refresh()

    def _load_state(self):
        conn = sqlite3.connect(os.path.join(_BASE_DIR, 'projects.db'))
        try:
            row = conn.execute('''
                SELECT COALESCE(data_sent_to_client, 0), COALESCE(data_approved_by_client, 0),
                       COALESCE(invoice_sent, 0), COALESCE(boleto_sent, 0),
                       COALESCE(invoice_paid, 0)
                FROM projects WHERE id = ?''', (self.project_id,)).fetchone()
        finally:
            conn.close()
        columns = [s[0] for s in QP.SEGMENTS if s[0]]
        self.flags = dict(zip(columns, [bool(v) for v in (row or (0, 0, 0, 0, 0))]))

    def _save_flag(self, column, value):
        conn = sqlite3.connect(os.path.join(_BASE_DIR, 'projects.db'))
        try:
            conn.execute(f'UPDATE projects SET {column} = ? WHERE id = ?',
                         (1 if value else 0, self.project_id))
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------- UI

    def _build_ui(self):
        outer = ttk.Frame(self, padding=(22, 18))
        outer.pack(fill="both", expand=True)

        top = ttk.Frame(outer)
        top.pack(fill="x", pady=(0, 16))
        ttk.Label(top, text=self.estimate_number, style="Title.TLabel").pack(anchor="w")
        self.subtitle = ttk.Label(top, text=self.client, style="Muted.TLabel")
        self.subtitle.pack(anchor="w")

        columns = ttk.Frame(outer)
        columns.pack(fill="both", expand=True)

        left = ttk.Frame(columns)
        left.pack(side="left", fill="y", padx=(0, 26))
        ttk.Label(left, text="STAGES", font=(UI_FONT, 8, "bold"), foreground=C_FAINT).pack(anchor="w")
        ttk.Label(left, text="double-click to toggle", style="Muted.TLabel").pack(anchor="w", pady=(0, 10))
        for column, letter, label in QP.SEGMENTS:
            row = ttk.Frame(left)
            row.pack(fill="x", pady=4)
            box = tk.Canvas(row, width=20, height=20, highlightthickness=0, background=C_BG)
            box.pack(side="left")
            rect = rounded_rect(box, 2, 2, 18, 18, 5, fill=C_TODO, outline="")
            handler = (lambda e, col=column: self._toggle_segment(col))
            box.bind("<Double-1>", handler)
            text = ttk.Label(row, text=label, font=(UI_FONT, 10))
            text.pack(side="left", padx=10)
            text.bind("<Double-1>", handler)
            self.segment_boxes[letter] = (box, rect, column)

        middle = ttk.Frame(columns)
        middle.pack(side="left", fill="y", padx=(0, 26))
        ttk.Label(middle, text="COSTS", font=(UI_FONT, 8, "bold"), foreground=C_FAINT).pack(anchor="w")
        ttk.Label(middle, text="by category", style="Muted.TLabel").pack(anchor="w", pady=(0, 10))
        self.category_labels = {}
        for key, label in ((QP.COST_LABOR, "Labor"), (QP.COST_MAINTENANCE, "Maintenance"),
                           (QP.COST_PROFIT, "Profit"), (QP.COST_REAGENTS, "Reagents")):
            row = ttk.Frame(middle)
            row.pack(fill="x", pady=5)
            ttk.Label(row, text=label, width=14, font=(UI_FONT, 10)).pack(side="left")
            amount = ttk.Label(row, text="-", width=15, anchor="e", font=(UI_FONT, 10))
            amount.pack(side="left")
            self.category_labels[key] = amount
        ttk.Separator(middle, orient="horizontal").pack(fill="x", pady=8)
        total_row = ttk.Frame(middle)
        total_row.pack(fill="x")
        ttk.Label(total_row, text="Total", width=14, font=(UI_FONT, 10, "bold")).pack(side="left")
        self.total_lbl = ttk.Label(total_row, text="-", width=15, anchor="e",
                                   font=(UI_FONT, 10, "bold"))
        self.total_lbl.pack(side="left")

        right = ttk.Frame(columns)
        right.pack(side="right", fill="both", expand=True)
        head = ttk.Frame(right)
        head.pack(fill="x")
        ttk.Label(head, text="PAYEES", font=(UI_FONT, 8, "bold"),
                  foreground=C_FAINT).pack(side="left")
        self.paid_count = ttk.Label(head, text="", style="Muted.TLabel")
        self.paid_count.pack(side="right")
        ttk.Label(right, text="double-click a bubble to mark as paid",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        # Scrolls automatically once the list outgrows the panel.
        self.recipients = ScrollableList(right)
        self.recipients.pack(fill="both", expand=True)

        footer = ttk.Frame(self, padding=(22, 0, 22, 18))
        footer.pack(fill="x")
        ttk.Button(footer, text="Close", command=self.destroy,
                   style="Primary.TButton").pack(side="right")

    # -------------------------------------------------------------- actions

    def _toggle_segment(self, column):
        if column is None:
            messagebox.showinfo(
                "Automatic",
                "'Debts Settled' fills in on its own once every payee on this "
                "project has been marked as paid.",
                parent=self)
            return
        self.flags[column] = not self.flags.get(column, False)
        self._save_flag(column, self.flags[column])
        self._refresh()

    def _toggle_bubble(self, payee_id, currently_paid):
        QP.set_settled(self.project_id, payee_id, not currently_paid, self.current_user)
        self._refresh()

    def _refresh(self):
        lines, profit = QP.calculate_cost_lines(self.project_id)
        by_category = {QP.COST_LABOR: 0.0, QP.COST_MAINTENANCE: 0.0,
                       QP.COST_REAGENTS: 0.0, QP.COST_PROFIT: profit}
        for line in lines:
            by_category[line['cost_type']] = by_category.get(line['cost_type'], 0.0) + line['amount']
        for key, widget in self.category_labels.items():
            widget.config(text=format_br_currency(by_category.get(key, 0.0)))
        self.total_lbl.config(text=format_br_currency(sum(by_category.values())))

        resolved = QP.resolve_recipients(self.project_id, self.responsible)
        settlements = QP.get_settlements(self.project_id)
        paid_ids = {r['payee_id'] for r in resolved['recipients']
                    if settlements.get(r['payee_id'], {}).get('paid')}
        everyone_paid = bool(resolved['recipients']) and len(paid_ids) == len(resolved['recipients'])

        for letter, (box, rect, column) in self.segment_boxes.items():
            done = everyone_paid if column is None else self.flags.get(column, False)
            box.itemconfig(rect, fill=C_DONE if done else C_TODO)

        self.paid_count.config(text=f"{len(paid_ids)}/{len(resolved['recipients'])} paid"
                               if resolved['recipients'] else "")

        self.recipients.clear()
        self.bubbles = {}
        for recipient in resolved['recipients']:
            paid = recipient['payee_id'] in paid_ids
            row = ttk.Frame(self.recipients.inner)
            row.pack(fill="x", pady=3, padx=(0, 6))
            bubble = tk.Canvas(row, width=18, height=18, highlightthickness=0, background=C_BG)
            bubble.pack(side="left")
            oval = bubble.create_oval(3, 3, 16, 16, fill=C_DONE if paid else C_BG,
                                      outline=C_DONE if paid else "#C2C7CE", width=2)
            bubble.bind("<Double-1>",
                        lambda e, pid=recipient['payee_id'], p=paid: self._toggle_bubble(pid, p))
            name = ttk.Label(row, text=recipient['name'], font=(UI_FONT, 10),
                             foreground=C_MUTED if paid else C_TEXT)
            name.pack(side="left", padx=10)
            ttk.Label(row, text=format_br_currency(recipient['amount']),
                      font=(UI_FONT, 10), foreground=C_MUTED if paid else C_TEXT).pack(side="right")
            self.bubbles[recipient['payee_id']] = (bubble, oval)

        if resolved['unassigned'] > 0.005:
            ttk.Label(self.recipients.inner,
                      text=f"Unassigned: {format_br_currency(resolved['unassigned'])}",
                      style="Danger.TLabel").pack(anchor="w", pady=(10, 0))
