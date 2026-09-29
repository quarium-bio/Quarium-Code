import os
import sys
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox
import tkinter.font as tkfont

import QuariumPayees as QP

# When frozen by PyInstaller, __file__ resolves inside the temporary
# extraction folder rather than the exe's real folder, so paths built from
# it point at a throwaway location. Use the exe's directory instead.
_BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))

COLOR_DONE = "#2E9E5B"
COLOR_TODO = "#C9CDD2"
COLOR_EDGE = "#8A9099"
COLOR_PRIMARY = "#285D80"
COLOR_ROWLINE = "#E5E7EB"
COLOR_HILITE = "#EEF4F8"

ROW_HEIGHT = 30
BAR_WIDTH = 246
COL_EST, COL_CLIENT, COL_TOTAL, COL_BAR = 12, 130, 320, 470


def format_br_currency(value):
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        value = 0.0
    return "R$ " + f"{value:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')


class FinanceManager:
    def __init__(self, root, current_user="Unknown"):
        self.root = root
        self.current_user = current_user
        self.db_path = os.path.join(_BASE_DIR, 'projects.db')
        self.rows = []          # drawn rows, for hit-testing
        self._tooltip = None
        self._tooltip_key = None

        QP.init_all(current_user)
        self.create_ui()
        self.load_financial_data()

    # ------------------------------------------------------------------- UI

    def create_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        header = ttk.Frame(main)
        header.pack(fill="x")
        ttk.Label(header, text="Situacao Financeira dos Projetos",
                  font=("Helvetica", 14, "bold")).pack(side="left")
        ttk.Button(header, text="Atualizar", command=self.load_financial_data).pack(side="right")

        legend = tk.Canvas(main, height=22, highlightthickness=0)
        legend.pack(fill="x", pady=(6, 2))
        self._draw_legend(legend)

        list_frame = ttk.LabelFrame(main, text="Projetos", padding=6)
        list_frame.pack(fill="both", expand=True)

        col_head = tk.Canvas(list_frame, height=20, highlightthickness=0)
        col_head.pack(fill="x")
        for text, x in (("Orcamento", COL_EST), ("Cliente", COL_CLIENT),
                        ("Custo Total", COL_TOTAL), ("Progresso", COL_BAR)):
            col_head.create_text(x, 10, text=text, anchor="w",
                                 font=("Helvetica", 9, "bold"), fill="#555555")

        canvas_wrap = ttk.Frame(list_frame)
        canvas_wrap.pack(fill="both", expand=True)
        # A Canvas rather than a Treeview: Tk colours a whole cell, so a
        # per-segment coloured bar can only be drawn, not tabulated.
        self.canvas = tk.Canvas(canvas_wrap, highlightthickness=0, background="white")
        scroll = ttk.Scrollbar(canvas_wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda e: self._hide_tooltip())
        self.canvas.bind("<Double-1>", self._on_double_click)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

        totals_frame = ttk.LabelFrame(main, text="Resumo Geral", padding=6)
        totals_frame.pack(fill="x", pady=(10, 0))
        self.totals_tree = ttk.Treeview(totals_frame, columns=("Amount",), show="tree headings", height=5)
        self.totals_tree.heading("#0", text="Categoria")
        self.totals_tree.heading("Amount", text="Total")
        self.totals_tree.column("#0", width=220)
        self.totals_tree.column("Amount", width=160, anchor="e")
        self.totals_tree.pack(fill="x")

    def _draw_legend(self, canvas):
        x = 4
        for letter, label in [(s[1], s[2]) for s in QP.SEGMENTS]:
            canvas.create_rectangle(x, 5, x + 12, 17, fill=COLOR_TODO, outline=COLOR_EDGE)
            canvas.create_text(x + 17, 11, text=f"{letter} {label}", anchor="w",
                               font=("Helvetica", 8), fill="#333333")
            x += 150

    # ----------------------------------------------------------------- data

    def _segment_state(self, project_row, settled):
        """Six booleans, in order. F is derived from the payee bubbles."""
        state = []
        for column, _letter, _label in QP.SEGMENTS:
            state.append(bool(project_row.get(column)) if column else settled)
        return state

    def load_financial_data(self):
        self._hide_tooltip()
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
            cur.execute("ATTACH DATABASE ? AS clients_db",
                        (os.path.join(_BASE_DIR, 'clients.db'),))
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
            messagebox.showerror("Erro de Banco de Dados", f"Nao foi possivel carregar os dados: {e}")
            return

        y = 6
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

            self._draw_row(y, p_id, est_num, client or "Desconhecido", project_total, state, responsible)
            y += ROW_HEIGHT

        self.canvas.configure(scrollregion=(0, 0, COL_BAR + BAR_WIDTH + 90, max(y, 10)))

        for label, key in (("Reagentes", QP.COST_REAGENTS), ("Mao de Obra", QP.COST_LABOR),
                           ("Manutencao", QP.COST_MAINTENANCE), ("Lucro", QP.COST_PROFIT)):
            self.totals_tree.insert("", "end", text=label, values=(format_br_currency(totals[key]),))
        self.totals_tree.insert("", "end", text="TOTAL", values=(format_br_currency(grand),))

    def _fit(self, text, max_px):
        """Truncates to the measured pixel width, so a long client name can't
        run into the next column."""
        text = text or ""
        font = getattr(self, '_row_font', None)
        if font is None:
            font = self._row_font = tkfont.Font(family="Helvetica", size=10)
        if font.measure(text) <= max_px:
            return text
        ellipsis = "..."
        budget = max_px - font.measure(ellipsis)
        clipped = ""
        for ch in text:
            if font.measure(clipped + ch) > budget:
                break
            clipped += ch
        return clipped.rstrip() + ellipsis

    def _draw_row(self, y, p_id, est_num, client, total, state, responsible):
        c = self.canvas
        c.create_line(0, y + ROW_HEIGHT - 4, COL_BAR + BAR_WIDTH + 80, y + ROW_HEIGHT - 4,
                      fill=COLOR_ROWLINE)
        c.create_text(COL_EST, y + 10, text=self._fit(est_num, COL_CLIENT - COL_EST - 10),
                      anchor="w", font=("Helvetica", 10))
        c.create_text(COL_CLIENT, y + 10, text=self._fit(client, COL_TOTAL - COL_CLIENT - 10),
                      anchor="w", font=("Helvetica", 10))
        c.create_text(COL_TOTAL, y + 10, text=format_br_currency(total), anchor="w",
                      font=("Helvetica", 10))

        seg_w = BAR_WIDTH / len(state)
        segments = []
        for i, done in enumerate(state):
            x0 = COL_BAR + i * seg_w
            rect = c.create_rectangle(x0, y + 3, x0 + seg_w - 1, y + 18,
                                      fill=COLOR_DONE if done else COLOR_TODO,
                                      outline=COLOR_EDGE)
            segments.append((x0, x0 + seg_w - 1, i, rect))
        c.create_text(COL_BAR + BAR_WIDTH + 12, y + 10,
                      text=f"{sum(state)}/{len(state)}", anchor="w",
                      font=("Helvetica", 8), fill="#666666")

        self.rows.append({'y0': y, 'y1': y + ROW_HEIGHT - 4, 'project_id': p_id,
                          'estimate': est_num, 'client': client, 'responsible': responsible,
                          'segments': segments})

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
        if row:
            for x0, x1, index, _rect in row['segments']:
                if x0 <= x <= x1:
                    key = (row['project_id'], index)
                    if key != self._tooltip_key:
                        letter, label = QP.SEGMENTS[index][1], QP.SEGMENTS[index][2]
                        self._show_tooltip(event, f"{letter}  {label}")
                        self._tooltip_key = key
                    return
        self._hide_tooltip()

    def _show_tooltip(self, event, text):
        self._hide_tooltip(keep_key=True)
        tip = tk.Toplevel(self.canvas)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{event.x_root + 14}+{event.y_root + 18}")
        tk.Label(tip, text=text, background="#333333", foreground="white",
                 font=("Helvetica", 9), padx=8, pady=3).pack()
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
        self._hide_tooltip()
        dialog = ProjectFinanceDialog(self.root, row['project_id'], row['estimate'],
                                      row['client'], row['responsible'], self.current_user)
        self.root.wait_window(dialog)
        self.load_financial_data()

    def on_closing(self):
        self._hide_tooltip()


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

        self.title(f"Detalhamento Financeiro - {estimate_number}")
        self.geometry("980x560")
        self.transient(parent)
        self.grab_set()

        self._load_state()
        self._build_ui()
        self._refresh()

    # ---------------------------------------------------------------- state

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
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        left = ttk.Frame(frm)
        left.pack(side="left", fill="y", padx=(0, 14))

        ttk.Label(left, text=f"Projeto:  {self.estimate_number}",
                  font=("Helvetica", 11, "bold")).pack(anchor="w")
        ttk.Label(left, text=f"Cliente:  {self.client}").pack(anchor="w")
        self.total_lbl = ttk.Label(left, text="Custo Total:  -", font=("Helvetica", 10, "bold"))
        self.total_lbl.pack(anchor="w", pady=(0, 10))

        ttk.Label(left, text="Etapas  (duplo clique para marcar)",
                  font=("Helvetica", 9), foreground="#666").pack(anchor="w", pady=(4, 4))

        for column, letter, label in QP.SEGMENTS:
            row = ttk.Frame(left)
            row.pack(fill="x", pady=3)
            box = tk.Canvas(row, width=22, height=22, highlightthickness=0)
            box.pack(side="left")
            rect = box.create_rectangle(2, 2, 20, 20, fill=COLOR_TODO, outline=COLOR_EDGE, width=2)
            box.bind("<Double-1>", lambda e, col=column: self._toggle_segment(col))
            ttk.Label(row, text=f"{letter}  {label}").pack(side="left", padx=8)
            self.segment_boxes[letter] = (box, rect, column)

        ttk.Separator(frm, orient="vertical").pack(side="left", fill="y", padx=6)

        middle = ttk.Frame(frm)
        middle.pack(side="left", fill="y", padx=14)
        ttk.Label(middle, text="Custos por Categoria",
                  font=("Helvetica", 11, "bold")).pack(anchor="w", pady=(0, 10))
        self.category_labels = {}
        for key, label in ((QP.COST_LABOR, "Mao de Obra"), (QP.COST_MAINTENANCE, "Manutencao"),
                           (QP.COST_PROFIT, "Lucro"), (QP.COST_REAGENTS, "Reagentes")):
            row = ttk.Frame(middle)
            row.pack(fill="x", pady=6)
            ttk.Label(row, text=label, width=14, font=("Helvetica", 10, "bold")).pack(side="left")
            amount = ttk.Label(row, text="-", width=14, anchor="e")
            amount.pack(side="left")
            self.category_labels[key] = amount

        ttk.Label(middle, text="Os botoes de edicao de atribuicao\nchegam na proxima etapa.",
                  font=("Helvetica", 8), foreground="#999", justify="left").pack(anchor="w", pady=(14, 0))

        right = ttk.LabelFrame(frm, text="Beneficiarios", padding=8)
        right.pack(side="right", fill="both", expand=True)
        ttk.Label(right, text="Duplo clique na bolha para marcar como pago",
                  font=("Helvetica", 8), foreground="#666").pack(anchor="w", pady=(0, 6))
        self.recipients_frame = ttk.Frame(right)
        self.recipients_frame.pack(fill="both", expand=True)

        btns = ttk.Frame(self, padding=(12, 0, 12, 12))
        btns.pack(fill="x")
        ttk.Button(btns, text="Fechar", command=self.destroy).pack(side="right")

    # -------------------------------------------------------------- actions

    def _toggle_segment(self, column):
        if column is None:
            messagebox.showinfo(
                "Automatico",
                "'Debts Settled' e preenchido automaticamente quando todos os "
                "beneficiarios do projeto estiverem marcados como pagos.",
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
        self.total_lbl.config(text=f"Custo Total:  {format_br_currency(sum(by_category.values()))}")

        resolved = QP.resolve_recipients(self.project_id, self.responsible)
        settlements = QP.get_settlements(self.project_id)
        everyone_paid = bool(resolved['recipients']) and all(
            settlements.get(r['payee_id'], {}).get('paid') for r in resolved['recipients'])

        for letter, (box, rect, column) in self.segment_boxes.items():
            done = everyone_paid if column is None else self.flags.get(column, False)
            box.itemconfig(rect, fill=COLOR_DONE if done else COLOR_TODO)

        for child in self.recipients_frame.winfo_children():
            child.destroy()
        self.bubbles = {}

        for recipient in resolved['recipients']:
            paid = bool(settlements.get(recipient['payee_id'], {}).get('paid'))
            row = ttk.Frame(self.recipients_frame)
            row.pack(fill="x", pady=3)
            bubble = tk.Canvas(row, width=18, height=18, highlightthickness=0)
            bubble.pack(side="left")
            oval = bubble.create_oval(2, 2, 16, 16,
                                      fill=COLOR_DONE if paid else "white", outline=COLOR_EDGE, width=2)
            bubble.bind("<Double-1>",
                        lambda e, pid=recipient['payee_id'], p=paid: self._toggle_bubble(pid, p))
            ttk.Label(row, text=recipient['name'], width=26).pack(side="left", padx=6)
            ttk.Label(row, text=format_br_currency(recipient['amount']),
                      width=16, anchor="e").pack(side="left")
            self.bubbles[recipient['payee_id']] = (bubble, oval)

        if resolved['unassigned'] > 0.005:
            ttk.Label(self.recipients_frame,
                      text=f"Sem beneficiario definido: {format_br_currency(resolved['unassigned'])}",
                      foreground="#B71C1C", font=("Helvetica", 9)).pack(anchor="w", pady=(10, 0))
