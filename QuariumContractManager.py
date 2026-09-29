import os
import re
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import sqlite3
from datetime import datetime
import copy
import webbrowser
import tempfile
import json
import threading

# Working data lives under %LOCALAPPDATA%, not beside the program: keeping
# live SQLite files inside the OneDrive-synced project folder meant two sync
# engines replicating the same open databases. Source runs get a separate
# workspace so testing cannot disturb live data.
from QuariumPaths import data_dir

_BASE_DIR = data_dir()

try:
    import pythoncom
    PYTHONCOM_AVAILABLE = True
except ImportError:
    PYTHONCOM_AVAILABLE = False

try:
    from docx.shared import RGBColor, Inches, Pt
    from docx.opc import constants as OPC_CONSTANTS
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    import docx
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

try:
    import docx2pdf
    DOCX2PDF_AVAILABLE = True
except ImportError:
    DOCX2PDF_AVAILABLE = False

try:
    from num2words import num2words
    NUM2WORDS_AVAILABLE = True
except ImportError:
    NUM2WORDS_AVAILABLE = False

class ContractManager:
    # Groups placeholders into labeled sections for the confirmation dialog,
    # so the ~25 fields read as a form instead of a flat unlabeled wall.
    _CONFIRMATION_SECTIONS = [
        ("Cliente", [
            "{{CLIENT_NAME}}", "{{CLIENT_TITLE}}", "{{CLIENT_ENTITY_TYPE}}", "{{COMPANY_NAME}}",
            "{{CLIENT_EMAIL}}", "{{CLIENT_PHONE}}", "{{CLIENT_ADDRESS}}",
            "{{CLIENT_CPF}}", "{{CLIENT_RG}}", "{{CLIENT_RG_ISSUER}}",
            "{{CLIENT_NATIONALITY}}", "{{CLIENT_MARITAL_STATUS}}", "{{CLIENT_PROFESSION}}",
        ]),
        ("Projeto", [
            "{{ESTIMATE_NUMBER}}", "{{PROJECT_DESCRIPTION}}", "{{PROJECT_COST}}",
            "{{PROJECT_COST_EXTENDED}}", "{{EXTRA_LINE}}", "{{CURRENT_DATE}}",
        ]),
        ("Quarium (Contratada)", [
            "{{QUARIUM_NAME}}", "{{QUARIUM_CNPJ}}", "{{QUARIUM_ADDRESS}}",
            "{{QUARIUM_REP_NAME}}", "{{QUARIUM_REP_NATIONALITY}}", "{{QUARIUM_REP_MARITAL_STATUS}}",
            "{{QUARIUM_REP_PROFESSION}}", "{{QUARIUM_REP_ID}}", "{{QUARIUM_REP_ID_ISSUER}}", "{{QUARIUM_REP_CPF}}",
        ]),
    ]

    def __init__(self, root, current_user="Unknown", drive_sync=None):
        self.root = root
        self.current_user = current_user
        self.drive_sync = drive_sync
        if isinstance(self.root, (tk.Tk, tk.Toplevel)):
            self.root.title("Quarium Contract Manager")
            self.root.geometry("1000x700")

        self.project_db_path = os.path.join(_BASE_DIR, 'projects.db')
        self.client_db_path = os.path.join(_BASE_DIR, 'clients.db')
        self.shell_template_path = os.path.join(_BASE_DIR, 'ContractShell.docx')
        self.header_template_path_pf = os.path.join(_BASE_DIR, 'ContractHeader_PF.docx')
        self.header_template_path_pj = os.path.join(_BASE_DIR, 'ContractHeader_PJ.docx')
        self.template_path_pf = os.path.join(_BASE_DIR, 'ContractTemplate_PF.docx')
        self.template_path_pj = os.path.join(_BASE_DIR, 'ContractTemplate_PJ.docx')
        self.settings_path = os.path.join(_BASE_DIR, 'settings.json')

        self.placeholders = {
            "{{CLIENT_NAME}}": "Full name of the client.",
            "{{CLIENT_TITLE}}": "Client's title (e.g., Dr., Ms.).",
            "{{CLIENT_EMAIL}}": "Client's email address.",
            "{{CLIENT_PHONE}}": "Client's phone number.",
            "{{CLIENT_ADDRESS}}": "Client's address.",
            "{{CLIENT_CPF}}": "Client's CPF.",
            "{{CLIENT_RG}}": "Client's RG.",
            "{{CLIENT_RG_ISSUER}}": "Client's RG issuer.",
            "{{CLIENT_NATIONALITY}}": "Client's nationality.",
            "{{CLIENT_MARITAL_STATUS}}": "Client's marital status.",
            "{{CLIENT_PROFESSION}}": "Client's profession.",
            "{{CLIENT_ENTITY_TYPE}}": "Client's legal type (e.g., pessoa física).",
            "{{PROJECT_DESCRIPTION}}": "The description of the project.",
            "{{EXTRA_LINE}}": "An optional, manually-entered line of text.",
            "{{COMPANY_NAME}}": "Name of the client's company.",
            "{{ESTIMATE_NUMBER}}": "The project's estimate number.",
            "{{PROJECT_COST}}": "The final cost of the project (e.g., R$ 1.234,56).",
            "{{PROJECT_COST_EXTENDED}}": "The final cost written out in words (e.g., um mil duzentos e trinta e quatro reais e cinquenta e seis centavos).",
            "{{CURRENT_DATE}}": "The current date (DD/MM/YYYY).",
            "---": "---", # Separator
            "{{QUARIUM_NAME}}": "Your company's full name from settings.",
            "{{QUARIUM_CNPJ}}": "Your company's CNPJ from settings.",
            "{{QUARIUM_ADDRESS}}": "Your company's address from settings.",
            "{{QUARIUM_REP_NAME}}": "Your legal representative's name.",
            "{{QUARIUM_REP_NATIONALITY}}": "Representative's nationality.",
            "{{QUARIUM_REP_MARITAL_STATUS}}": "Representative's marital status.",
            "{{QUARIUM_REP_PROFESSION}}": "Representative's profession.",
            "{{QUARIUM_REP_ID}}": "Representative's ID (RG).",
            "{{QUARIUM_REP_ID_ISSUER}}": "The issuer of the representative's ID.",
            "{{QUARIUM_REP_CPF}}": "Representative's CPF.",
        }

        self.load_settings()
        self.create_ui()
        self.load_approved_projects()
        self.update_placeholder_tree()
        self._check_pj_templates()

    def create_ui(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill="both", expand=True)

        # --- Top Frame for Project Selection ---
        top_frame = ttk.LabelFrame(main_frame, text="Select Project", padding=10)
        top_frame.pack(fill="x", pady=(0, 10))

        ttk.Label(top_frame, text="Approved Projects (use Ctrl or Shift to select multiple):").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        
        list_frame = ttk.Frame(top_frame)
        list_frame.grid(row=0, column=1, padx=5, pady=5, sticky="nsew")
        top_frame.grid_columnconfigure(1, weight=1)

        self.project_listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED, height=5, exportselection=False)
        self.project_listbox.pack(side="left", fill="both", expand=True)
        
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.project_listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.project_listbox.config(yscrollcommand=scrollbar.set)
        self.project_listbox.bind("<<ListboxSelect>>", self.on_project_selected)

        # --- Middle Frame for Template and Placeholders ---
        mid_frame = ttk.Frame(main_frame)
        mid_frame.pack(fill="both", expand=True)

        # Left side: Template Editor
        template_frame = ttk.LabelFrame(mid_frame, text="Contract Content Templates", padding=10)
        template_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))

        ttk.Label(template_frame, text="The contract is built from multiple templates. Use the buttons below to edit each part.", wraplength=400).pack(pady=(0,10), fill="x")

        header_controls = ttk.Frame(template_frame)
        header_controls.pack(fill="x", pady=5)
        ttk.Label(header_controls, text="Header (Parties):").pack(side="left", anchor="w", padx=(0,10))
        ttk.Button(header_controls, text="Open PF Header", command=lambda: self.open_template(self.header_template_path_pf)).pack(side="left", padx=5)
        ttk.Button(header_controls, text="Open PJ Header", command=lambda: self.open_template(self.header_template_path_pj)).pack(side="left", padx=5)

        body_controls = ttk.Frame(template_frame)
        body_controls.pack(fill="x", pady=5)
        ttk.Label(body_controls, text="Body (Clauses):").pack(side="left", anchor="w", padx=(0,10))
        ttk.Button(body_controls, text="Open PF Body", command=lambda: self.open_template(self.template_path_pf)).pack(side="left", padx=5)
        ttk.Button(body_controls, text="Open PJ Body", command=lambda: self.open_template(self.template_path_pj)).pack(side="left", padx=5)

        shell_controls = ttk.Frame(template_frame)
        shell_controls.pack(fill="x", pady=5)
        ttk.Label(shell_controls, text="Layout (Shell):").pack(side="left", anchor="w")
        ttk.Button(shell_controls, text="Open Layout Template", command=lambda: self.open_template(self.shell_template_path)).pack(side="left", padx=5)

        ttk.Button(template_frame, text="Open Template Folder", command=self.open_template_folder).pack(pady=10)

        # Right side: Placeholders
        placeholder_frame = ttk.LabelFrame(mid_frame, text="Available Placeholders", padding=10)
        placeholder_frame.pack(side="right", fill="y")

        self.placeholder_tree = ttk.Treeview(placeholder_frame, columns=("Description",), height=15)
        self.placeholder_tree.heading("#0", text="Placeholder")
        self.placeholder_tree.heading("Description", text="Description")
        self.placeholder_tree.column("#0", width=150)
        self.placeholder_tree.column("Description", width=200)
        self.placeholder_tree.pack(fill="y", expand=True)

        # --- Bottom Frame for PDF Generation ---
        bottom_frame = ttk.Frame(main_frame, padding=(0, 10, 0, 0))
        bottom_frame.pack(fill="x")

        self.generate_btn = ttk.Button(bottom_frame, text="Generate Contract PDF", command=self.generate_pdf, style="Accent.TButton", state="disabled")
        self.generate_btn.pack(side="right", ipady=5)

    def load_settings(self):
        self.settings = {}
        if os.path.exists(self.settings_path):
            try:
                with open(self.settings_path, 'r') as f:
                    self.settings = json.load(f)
            except (json.JSONDecodeError, IOError):
                messagebox.showwarning("Settings Error", "Could not read settings.json. Contract info may be missing.")

    def load_approved_projects(self):
        self.projects_data = {}
        self.project_listbox.delete(0, tk.END)
        conn = None
        try:
            conn = sqlite3.connect(self.project_db_path)
            cursor = conn.cursor()
            cursor.execute("ATTACH DATABASE ? AS clients_db", (self.client_db_path,))
            cursor.execute('''
                SELECT p.id, p.estimate_number, c.name
                FROM projects p
                LEFT JOIN clients_db.clients c ON p.client_id = c.id
                WHERE p.status >= 1 AND p.status < 7
                ORDER BY p.estimate_number DESC
            ''')
            for p_id, est_num, client_name in cursor.fetchall():
                display_name = f"{est_num} - {client_name or 'Unknown Client'}"
                self.projects_data[display_name] = p_id
                self.project_listbox.insert(tk.END, display_name)
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not load projects: {e}")
        finally:
            if conn:
                conn.close()

    def update_placeholder_tree(self):
        for item in self.placeholder_tree.get_children():
            self.placeholder_tree.delete(item)
        for key, desc in self.placeholders.items():
            if key == "---":
                self.placeholder_tree.insert("", "end", text="-"*20)
            else:
                self.placeholder_tree.insert("", "end", text=key, values=(desc,))

    def on_project_selected(self, event=None):
        if self.project_listbox.curselection():
            self.generate_btn.config(state="normal")
        else:
            self.generate_btn.config(state="disabled")

    def open_template(self, path):
        if not os.path.exists(path):
            messagebox.showwarning("Template Missing", f"{os.path.basename(path)} not found in the application folder.")
            return
        try:
            os.startfile(path)
        except AttributeError:
            webbrowser.open(path)

    def open_template_folder(self):
        """Opens the folder containing the contract template."""
        webbrowser.open(_BASE_DIR)

    def get_project_data(self, project_id):
        data = {}
        conn = None
        try:
            conn = sqlite3.connect(self.project_db_path)
            cursor = conn.cursor()
            cursor.execute("ATTACH DATABASE ? AS clients_db", (self.client_db_path,))
            cursor.execute('''
                SELECT p.estimate_number, p.final_cost, p.description,
                       c.name, c.email, c.phone, c.address, c.cpf, c.rg, c.rg_issuer, c.nationality, c.marital_status, c.profession, c.title,
                       comp.name, comp.cnpj, comp.address, comp.is_legal_entity
                FROM projects p
                LEFT JOIN clients_db.clients c ON p.client_id = c.id
                LEFT JOIN clients_db.companies comp ON c.company_id = comp.id
                WHERE p.id = ?
            ''', (project_id,))
            row = cursor.fetchone()

            if row:
                (est_num, cost, proj_desc, c_name, c_email, c_phone, c_addr, c_cpf, c_rg, c_rg_issuer, c_nationality, c_marital_status, c_profession,
                 c_title, comp_name, comp_cnpj, comp_addr, is_legal) = row

                data["{{CLIENT_NAME}}"] = c_name or ""
                if is_legal:
                    data["{{CLIENT_NAME}}"] = comp_name or "" # If company is the client, use its name
                    data["{{CLIENT_ENTITY_TYPE}}"] = "pessoa jurídica de direito privado"
                else:
                    data["{{CLIENT_ENTITY_TYPE}}"] = "pessoa física"
                data["{{CLIENT_TITLE}}"] = c_title or ""
                data["{{CLIENT_EMAIL}}"] = c_email or ""
                data["{{CLIENT_PHONE}}"] = c_phone or ""
                data["{{CLIENT_ADDRESS}}"] = c_addr or ""
                data["{{COMPANY_NAME}}"] = comp_name or ""
                data["{{ESTIMATE_NUMBER}}"] = est_num or ""
                formatted_cost = f"{cost:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
                data["{{PROJECT_COST}}"] = f"R$ {formatted_cost}"
                data["{{PROJECT_COST_EXTENDED}}"] = self.number_to_words(cost)
                data["{{PROJECT_DESCRIPTION}}"] = proj_desc or ""
                data["{{CLIENT_CPF}}"] = c_cpf or ""
                data["{{CLIENT_RG}}"] = c_rg or ""
                data["{{CLIENT_RG_ISSUER}}"] = c_rg_issuer or ""
                data["{{CLIENT_NATIONALITY}}"] = c_nationality or ""
                data["{{CLIENT_MARITAL_STATUS}}"] = c_marital_status or ""
                data["{{CLIENT_PROFESSION}}"] = c_profession or ""
                data["{{CURRENT_DATE}}"] = datetime.now().strftime('%d/%m/%Y')

                # Add company info from settings
                contract_info = self.settings.get("contract_info", {})
                data["{{QUARIUM_NAME}}"] = contract_info.get("company_name", "")
                data["{{QUARIUM_CNPJ}}"] = contract_info.get("company_cnpj", "")
                data["{{QUARIUM_ADDRESS}}"] = contract_info.get("company_address", "")
                data["{{QUARIUM_REP_NAME}}"] = contract_info.get("rep_name", "")
                data["{{QUARIUM_REP_NATIONALITY}}"] = contract_info.get("rep_nationality", "")
                data["{{QUARIUM_REP_MARITAL_STATUS}}"] = contract_info.get("rep_marital_status", "")
                data["{{QUARIUM_REP_PROFESSION}}"] = contract_info.get("rep_profession", "")
                data["{{QUARIUM_REP_ID}}"] = contract_info.get("rep_id", "")
                data["{{QUARIUM_REP_ID_ISSUER}}"] = contract_info.get("rep_id_issuer", "")
                data["{{QUARIUM_REP_CPF}}"] = contract_info.get("rep_cpf", "")

            return data
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not fetch project data: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def format_br_currency(self, value):
        formatted = f"{value:,.2f}"
        formatted = formatted.replace(',', 'X').replace('.', ',').replace('X', '.')
        return f"R$ {formatted}"

    def number_to_words(self, n):
        """Converts a number to Brazilian Portuguese currency words. Assumes num2words is installed."""
        # Round to the nearest cent first: splitting a raw float into reais/centavos
        # (e.g. int(n) + round((n - int(n)) * 100)) can hit values like
        # 65261.999999999996 from summed currency floats, truncating a whole real
        # and reporting "100 centavos" instead of carrying it over.
        total_cents = round(n * 100)
        reais, centavos = divmod(total_cents, 100)
        reais_txt = num2words(reais, lang='pt_BR') + (' reais' if reais != 1 else ' real')
        centavos_txt = num2words(centavos, lang='pt_BR') + (' centavos' if centavos != 1 else ' centavo')
        return f"({reais_txt} e {centavos_txt})" if centavos > 0 else f"({reais_txt})"

    def _check_pj_templates(self):
        """Warns at startup if the pessoa jurídica templates are missing, instead
        of letting a user only discover it mid-generation. Skipped when drive_sync
        is configured, since _ensure_templates_are_present can fetch them on demand."""
        if self.drive_sync:
            return
        missing = [os.path.basename(p) for p in (self.header_template_path_pj, self.template_path_pj) if not os.path.exists(p)]
        if missing:
            messagebox.showwarning(
                "Pessoa Jurídica Templates Missing",
                "The following templates for pessoa jurídica (company) contracts were not found:\n"
                + "\n".join(f"  - {m}" for m in missing)
                + "\n\nContracts can still be generated for pessoa física clients, but company "
                  "contracts will fail until these are added to the application folder."
            )

    def _ensure_templates_are_present(self):
        """Checks for contract templates and downloads them if missing and sync is enabled."""
        if not self.drive_sync:
            return True # In local mode, we can't download them.

        required_templates = [self.shell_template_path, self.header_template_path_pf, self.header_template_path_pj, 
                              self.template_path_pf, self.template_path_pj]
        
        missing_templates = []
        for template_path in required_templates:
            if not os.path.exists(template_path):
                missing_templates.append(os.path.basename(template_path))
        
        if missing_templates:
            messagebox.showinfo("Downloading Templates", "One or more contract templates are missing. Downloading the latest versions from the cloud...")
            self.drive_sync.sync_down(missing_templates)
        
        return True

    def generate_pdf(self):
        if not DOCX_AVAILABLE:
            messagebox.showerror("Dependency Missing", "Please install python-docx to generate contracts.\nCommand: pip install python-docx")
            return
        if not DOCX2PDF_AVAILABLE:
            messagebox.showerror("Dependency Missing", "Please install docx2pdf to generate PDFs.\nCommand: pip install docx2pdf")
            return
        if not NUM2WORDS_AVAILABLE:
            messagebox.showerror("Dependency Missing", "Please install 'num2words' to write out the project cost.\nCommand: pip install num2words")
            return
        
        # On-demand download of templates
        self._ensure_templates_are_present()

        # --- Get Selected Projects ---
        selections = self.project_listbox.curselection()
        if not selections:
            messagebox.showwarning("Warning", "Please select at least one project.")
            return

        selected_project_ids = [self.projects_data[self.project_listbox.get(i)] for i in selections]
        all_project_data = [self.get_project_data(pid) for pid in selected_project_ids]

        if not all(all_project_data):
            messagebox.showerror("Error", "Could not fetch data for one or more selected projects.")
            return

        # --- Validate and Prepare Data ---
        # If the selected projects span more than one client/company, ask which
        # one should be the CONTRATANTE (payer) on this contract. Returns a
        # fresh copy, since the summary table loop below reads all_project_data
        # per-project and shouldn't see the aggregated totals written into it.
        primary_project_data = self._ask_payer_selection(all_project_data)
        if primary_project_data is None:
            return # User cancelled

        total_cost = sum(float(p.get("{{PROJECT_COST}}", "R$ 0.00").replace("R$ ", "").replace(".", "").replace(",", ".")) for p in all_project_data)

        primary_project_data["{{PROJECT_COST}}"] = self.format_br_currency(total_cost)
        primary_project_data["{{PROJECT_COST_EXTENDED}}"] = self.number_to_words(total_cost)
        primary_project_data["{{ESTIMATE_NUMBER}}"] = ", ".join([p.get("{{ESTIMATE_NUMBER}}", "") for p in all_project_data])

        # Determine which header template to use based on client type
        if primary_project_data.get("{{CLIENT_ENTITY_TYPE}}") == "pessoa física":
            header_template_path = self.header_template_path_pf
            body_template_path = self.template_path_pf
        else:
            header_template_path = self.header_template_path_pj
            body_template_path = self.template_path_pj

        # Check for all required templates
        required_templates = [self.shell_template_path, header_template_path, body_template_path]
        for tpl_path in required_templates:
            if not os.path.exists(tpl_path):
                messagebox.showerror("Template Missing", f"Required template '{os.path.basename(tpl_path)}' not found.")
                return

        # Show confirmation/edit dialog
        final_data = self._show_confirmation_dialog(primary_project_data)
        if not final_data:
            return # User cancelled

        try:
            # 1. Load the shell document
            final_doc = docx.Document(self.shell_template_path)
            # 2. Replace placeholders in the shell's own headers/footers
            for section in final_doc.sections:
                self._replace_placeholders_in_part(section.header, final_data)
                self._replace_placeholders_in_part(section.footer, final_data)
                # Give the footer some breathing room from the page content above it
                if section.footer.paragraphs:
                    section.footer.paragraphs[0].paragraph_format.space_before = Pt(10)

            # 3. Find the insertion points in the shell document
            header_insertion_point = self._find_insertion_paragraph(final_doc, '{{INSERT_HEADER_HERE}}')
            body_insertion_point = self._find_insertion_paragraph(final_doc, '{{INSERT_BODY_HERE}}')

            if not header_insertion_point or not body_insertion_point:
                messagebox.showerror("Template Error", "The ContractShell.docx template must contain the placeholders '{{INSERT_HEADER_HERE}}' and '{{INSERT_BODY_HERE}}'.")
                return

            # --- Add Custom Header Content ---
            logo_path = os.path.join(_BASE_DIR, 'QLogo.png')
            if os.path.exists(logo_path):
                p_logo = header_insertion_point.insert_paragraph_before()
                p_logo.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run_logo = p_logo.add_run()
                run_logo.add_picture(logo_path, width=Inches(1.5))

            p_title = header_insertion_point.insert_paragraph_before()
            p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run_title = p_title.add_run("TERMO DE ACEITE DE SERVIÇO")
            run_title.bold = True
            run_title.font.size = Pt(14)

            # 4. Process templates first, then insert them.
            header_doc = docx.Document(header_template_path)
            self._replace_placeholders_in_part(header_doc, final_data)
            self._insert_doc_at_paragraph(header_doc, header_insertion_point)

            body_doc = docx.Document(body_template_path)
            self._replace_placeholders_in_part(body_doc, final_data)
            self._insert_doc_at_paragraph(body_doc, body_insertion_point)

            # --- Add Project Summary Table ---
            final_doc.add_paragraph()
            final_doc.add_heading("Resumo dos Serviços Contratados", level=2)
            table = final_doc.add_table(rows=1, cols=3)
            table_font_size = Pt(9)
            hdr_cells = table.rows[0].cells
            hdr_cells[0].text = 'N° Orçamento'
            hdr_cells[1].text = 'Descrição do Projeto'
            hdr_cells[2].text = 'Custo'
            for cell in hdr_cells:
                run = cell.paragraphs[0].runs[0]
                run.bold = True
                run.font.size = table_font_size

            for p_data in all_project_data:
                row_cells = table.add_row().cells
                row_cells[0].text = p_data.get("{{ESTIMATE_NUMBER}}", "")
                row_cells[1].text = p_data.get("{{PROJECT_DESCRIPTION}}", "N/A")
                row_cells[2].text = p_data.get("{{PROJECT_COST}}", "R$ 0.00")
                for cell in row_cells:
                    cell.paragraphs[0].runs[0].font.size = table_font_size

            # Add total row
            total_row = table.add_row().cells
            total_row[0].merge(total_row[1])
            total_row[0].text = "Custo Total"
            total_row[0].paragraphs[0].runs[0].bold = True
            total_row[2].text = self.format_br_currency(total_cost)
            total_row[2].paragraphs[0].runs[0].bold = True
            for cell in (total_row[0], total_row[2]):
                cell.paragraphs[0].runs[0].font.size = table_font_size

            # Size columns to fit their contents (estimate # and cost are short,
            # the description needs the most room) and add a visible grid so
            # the table reads clearly on the page.
            self._set_column_widths(table, [Inches(1.2), Inches(3.8), Inches(1.5)])
            self._set_table_borders(table)

            # --- Add Signature Section ---
            final_doc.add_paragraph()
            final_doc.add_paragraph()

            sig_table = final_doc.add_table(rows=2, cols=2)
            
            # CONTRATANTE cell (left)
            cell_contte = sig_table.cell(0, 0)
            p_contte = cell_contte.add_paragraph()
            p_contte.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_contte.add_run("______________________________________\n")
            contratante_title = final_data.get("{{CLIENT_TITLE}}", "")
            contratante_name = final_data.get("{{CLIENT_NAME}}", "")
            run_contte_name = p_contte.add_run(f"{contratante_title} {contratante_name}".strip())
            run_contte_name.bold = True
            run_contte_name.font.size = Pt(10)
            p_contte.add_run("\nCONTRATANTE")
            # Add date for CONTRATANTE
            p_contte_date = cell_contte.add_paragraph()
            p_contte_date.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_contte_date.add_run("\nLocal e Data: ____________________, ___ de _______________ de 20__.")

            # CONTRATADA cell (right)
            cell_contda = sig_table.cell(0, 1)
            p_contda = cell_contda.add_paragraph()
            p_contda.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_contda.add_run("______________________________________\n")
            contratada_name = final_data.get("{{QUARIUM_NAME}}", "Quarium Consultoria em Biologia Analítica")
            run_contda_name = p_contda.add_run(contratada_name)
            run_contda_name.bold = True
            run_contda_name.font.size = Pt(10)
            p_contda.add_run("\nCONTRATADA")
            # Add date for CONTRATADA
            p_contda_date = cell_contda.add_paragraph()
            p_contda_date.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_contda_date.add_run("\nLocal e Data: ____________________, ___ de _______________ de 20__.")

            # 6. Remove the original insertion point paragraphs
            self._delete_paragraph(header_insertion_point)
            self._delete_paragraph(body_insertion_point)

            # header_doc/body_doc had their placeholders replaced before insertion
            # (step 4), the shell's own header/footer were handled in step 2, and
            # the shell body itself carries no other placeholders - so nothing
            # left in final_doc still needs a replacement pass.

        except Exception as e:
            messagebox.showerror("Template Error", f"Failed to read or process the .docx template:\n{e}")
            return

        # --- Save the final combined document to PDF ---
        est_num = final_data.get("{{ESTIMATE_NUMBER}}", "contract")
        filepath = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            initialfile=f"Contrato_{est_num}.pdf",
            filetypes=[("PDF Files", "*.pdf")]
        )
        if not filepath:
            return

        self._convert_to_pdf_async(final_doc, filepath, est_num)

    def _convert_to_pdf_async(self, final_doc, filepath, est_num):
        """Drives Word (via docx2pdf/COM) on a background thread so the UI
        doesn't appear to freeze while a larger contract converts, showing a
        progress dialog meanwhile."""
        # Use the application's directory for the temporary file to avoid permission issues
        # with the system's temp folder. The '~' prefix marks it for easy identification.
        temp_docx_path = os.path.join(_BASE_DIR, f"~temp_contract_{est_num}.docx")
        try:
            final_doc.save(temp_docx_path)
        except Exception as e:
            self._offer_docx_fallback(final_doc, est_num, f"Could not prepare the document for PDF conversion: {e}")
            return

        progress_dialog = self._show_progress_dialog("Gerando o contrato em PDF, aguarde...")
        self.generate_btn.config(state="disabled")

        def worker():
            error = None
            # COM requires each thread that uses it to initialize its own
            # apartment; docx2pdf drives Word via COM, and this runs off the
            # main thread, so it needs its own Co(Un)Initialize pair.
            if PYTHONCOM_AVAILABLE:
                pythoncom.CoInitialize()
            try:
                docx2pdf.convert(temp_docx_path, filepath)
            except Exception as e:
                error = e
            finally:
                if PYTHONCOM_AVAILABLE:
                    pythoncom.CoUninitialize()
                if os.path.exists(temp_docx_path):
                    os.remove(temp_docx_path)
            self.root.after(0, lambda: self._on_pdf_conversion_done(progress_dialog, error, final_doc, filepath, est_num))

        threading.Thread(target=worker, daemon=True).start()

    def _on_pdf_conversion_done(self, progress_dialog, error, final_doc, filepath, est_num):
        """Runs back on the main thread once the background conversion finishes."""
        progress_dialog.destroy()
        self.generate_btn.config(state="normal")

        if error is None:
            messagebox.showinfo("Success", f"Contract PDF generated successfully:\n{filepath}")
            return

        self._offer_docx_fallback(
            final_doc, est_num,
            f"An error occurred during PDF conversion: {error}\n\n"
            "This can happen if Microsoft Word is not installed, not activated, "
            "or has a dialog box open in the background."
        )

    def _offer_docx_fallback(self, final_doc, est_num, message):
        """Offers to save the in-memory document as .docx when PDF generation
        can't complete, so the work put into filling out the contract isn't lost."""
        if messagebox.askyesno("PDF Generation Error", f"{message}\n\nWould you like to save the generated Word document (.docx) instead?"):
            docx_filepath = filedialog.asksaveasfilename(
                defaultextension=".docx",
                initialfile=f"Contrato_{est_num}.docx",
                filetypes=[("Word Documents", "*.docx")]
            )
            if docx_filepath:
                try:
                    final_doc.save(docx_filepath)
                    messagebox.showinfo("Success", f"Word document saved successfully:\n{docx_filepath}")
                except Exception as save_e:
                    messagebox.showerror("Save Error", f"Could not save the .docx file: {save_e}")

    def _show_progress_dialog(self, message):
        """Shows a small modal 'please wait' dialog with an indeterminate
        progress bar, used while a background task runs."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Please Wait")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.protocol("WM_DELETE_WINDOW", lambda: None)  # no way to cancel a running conversion

        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=message).pack(pady=(0, 10))
        progress = ttk.Progressbar(frame, mode="indeterminate", length=280)
        progress.pack()
        progress.start(12)

        dialog.update_idletasks()
        w, h = dialog.winfo_width(), dialog.winfo_height()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (w // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (h // 2)
        dialog.geometry(f'{w}x{h}+{x}+{y}')
        return dialog

    def _ask_payer_selection(self, all_project_data):
        """If the selected projects span more than one client, asks which one
        should be listed as the CONTRATANTE (payer) on the contract. Returns a
        fresh copy of that client's project data, or None if the user cancels."""
        distinct_clients = []
        seen_names = set()
        for p in all_project_data:
            name = p.get("{{CLIENT_NAME}}") or "Unknown Client"
            if name not in seen_names:
                seen_names.add(name)
                distinct_clients.append((name, p))

        if len(distinct_clients) <= 1:
            return copy.deepcopy(all_project_data[0])

        dialog = tk.Toplevel(self.root)
        dialog.title("Select Payer")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        frame = ttk.Frame(dialog, padding=15)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text="The selected projects belong to different clients.\n"
                 "Who should be listed as CONTRATANTE (payer) on this contract?",
            justify="left", wraplength=420,
        ).pack(anchor="w", pady=(0, 10))

        selected_idx = tk.IntVar(value=0)
        for i, (name, _) in enumerate(distinct_clients):
            est_nums = ", ".join(
                p.get("{{ESTIMATE_NUMBER}}", "") for p in all_project_data
                if (p.get("{{CLIENT_NAME}}") or "Unknown Client") == name
            )
            ttk.Radiobutton(
                frame, variable=selected_idx, value=i,
                text=f"{name}  (Orçamento(s): {est_nums})",
            ).pack(anchor="w", pady=3)

        result = {"cancelled": True, "data": None}

        def on_confirm():
            result["cancelled"] = False
            result["data"] = copy.deepcopy(distinct_clients[selected_idx.get()][1])
            dialog.destroy()

        def on_cancel():
            dialog.destroy()

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", pady=(15, 0))
        ttk.Button(btn_frame, text="Confirm", command=on_confirm, style="Accent.TButton").pack(side="right")
        ttk.Button(btn_frame, text="Cancel", command=on_cancel).pack(side="right", padx=10)

        dialog.update_idletasks()
        w, h = dialog.winfo_width(), dialog.winfo_height()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (w // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (h // 2)
        dialog.geometry(f'{w}x{h}+{x}+{y}')

        self.root.wait_window(dialog)

        if result["cancelled"]:
            return None
        return result["data"]

    def _show_confirmation_dialog(self, initial_data):
        """Shows a dialog to confirm and edit contract data before generation."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Confirm Contract Details")
        dialog.geometry("700x600")
        dialog.transient(self.root)
        dialog.grab_set()

        # --- Scrollable Frame ---
        canvas = tk.Canvas(dialog)
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # --- Form Fields, grouped into labeled sections for readability ---
        string_vars = {}

        # Add the new optional field
        initial_data["{{EXTRA_LINE}}"] = ""

        def add_field(parent, row, key, value):
            label_text = key.replace("{{", "").replace("}}", "").replace("_", " ").title()
            ttk.Label(parent, text=f"{label_text}:").grid(row=row, column=0, sticky="w", padx=5, pady=3)
            var = tk.StringVar(value=str(value))
            ttk.Entry(parent, textvariable=var, width=55).grid(row=row, column=1, sticky="ew", padx=5, pady=3)
            string_vars[key] = var

        remaining_keys = set(initial_data.keys())
        for section_title, section_keys in self._CONFIRMATION_SECTIONS:
            section_frame = ttk.LabelFrame(scrollable_frame, text=section_title, padding=10)
            section_frame.pack(fill="x", padx=10, pady=(0, 10))
            section_frame.columnconfigure(1, weight=1)
            row = 0
            for key in section_keys:
                if key not in initial_data:
                    continue
                remaining_keys.discard(key)
                add_field(section_frame, row, key, initial_data[key])
                row += 1

        # Any field not covered by a known section still needs to be editable
        if remaining_keys:
            other_frame = ttk.LabelFrame(scrollable_frame, text="Outros", padding=10)
            other_frame.pack(fill="x", padx=10, pady=(0, 10))
            other_frame.columnconfigure(1, weight=1)
            for row, key in enumerate(sorted(remaining_keys)):
                add_field(other_frame, row, key, initial_data[key])

        # --- Buttons ---
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(side="bottom", fill="x", pady=10, padx=10)

        result = {"cancelled": True}

        def on_confirm():
            # Update the data dictionary from the StringVars
            for key, var in string_vars.items():
                initial_data[key] = var.get()
            result["cancelled"] = False
            dialog.destroy()

        def on_cancel():
            dialog.destroy()

        ttk.Button(btn_frame, text="Confirm & Generate PDF", command=on_confirm, style="Accent.TButton").pack(side="right")
        ttk.Button(btn_frame, text="Cancel", command=on_cancel).pack(side="right", padx=10)

        # Center the dialog
        dialog.update_idletasks()
        w = dialog.winfo_width()
        h = dialog.winfo_height()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (w // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (h // 2)
        dialog.geometry(f'{w}x{h}+{x}+{y}')

        self.root.wait_window(dialog)

        if result["cancelled"]:
            return None
        return initial_data

    def _replace_placeholders_in_part(self, doc_part, project_data):
        """Replaces placeholders in paragraphs and tables of a document part (doc, header, cell)."""
        # Process paragraphs
        paragraphs_to_delete = []
        for p in doc_part.paragraphs:
            # Special handling for {{EXTRA_LINE}}: if its value is empty, mark paragraph for deletion
            if "{{EXTRA_LINE}}" in p.text and not project_data.get("{{EXTRA_LINE}}"):
                if p.text.strip() == "{{EXTRA_LINE}}":
                    paragraphs_to_delete.append(p)
                    continue # Skip further processing for this paragraph
            
            # Simple run-by-run replacement to preserve formatting
            for run in p.runs:
                for key, value in project_data.items():
                    if key in run.text:
                        run.text = run.text.replace(key, str(value))

            # Word can silently split a "{{PLACEHOLDER}}" token across multiple
            # runs (e.g. after spell-check or manual re-formatting), so no single
            # run above ever contains the whole key and it's left untouched,
            # leaking literal placeholder text into the contract. Fall back to
            # collapsing the paragraph's runs when that happens, trading the
            # placeholder's own run-level formatting for actually resolving it.
            if any(key in p.text for key in project_data):
                full_text = p.text
                for key, value in project_data.items():
                    full_text = full_text.replace(key, str(value))
                for run in p.runs[1:]:
                    run.text = ""
                if p.runs:
                    p.runs[0].text = full_text
                elif full_text:
                    p.add_run(full_text)

        # Delete marked paragraphs after iteration to avoid modifying list during iteration
        for p in paragraphs_to_delete:
            self._delete_paragraph(p)

        # Process tables
        for table in doc_part.tables:
            for row in table.rows:
                for cell in row.cells:
                    self._replace_placeholders_in_part(cell, project_data)

    def _find_insertion_paragraph(self, document, placeholder_text):
        """Finds the paragraph object containing the placeholder."""
        for p in document.paragraphs:
            if placeholder_text in p.text:
                return p
        return None

    def _insert_doc_at_paragraph(self, source_doc, para):
        """Inserts all elements from source_doc before the given paragraph."""
        target_doc = para.part.document
        r_ns = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'

        # Iterate over all elements in the source document's body
        for element in source_doc.element.body:
            # The body-level sectPr defines page setup for the source
            # document and is only valid as the very last element of a
            # document body. Copying it into the middle of the merged body
            # produces a .docx Word reports as corrupt.
            if element.tag.endswith('}sectPr'):
                continue

            # Deepcopy the element to avoid modifying the original
            new_element = copy.deepcopy(element)

            # Any relationship reference carried over in the copied XML
            # (image embeds, hyperlinks, linked media, ...) still points at
            # a relationship id from source_doc's own package. Left as-is,
            # that id either resolves to nothing or to an unrelated
            # relationship in the target document, which is exactly what
            # makes Word report the file as corrupt / referring to objects
            # that don't exist. Re-create each relationship in the target
            # document and rewrite the id in place.
            for el in new_element.iter():
                for attr in (f'{r_ns}embed', f'{r_ns}id', f'{r_ns}link'):
                    rId = el.get(attr)
                    if not rId or rId not in source_doc.part.rels:
                        continue
                    rel = source_doc.part.rels[rId]
                    if rel.is_external:
                        new_rId = target_doc.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
                    else:
                        new_rId = target_doc.part.relate_to(rel.target_part, rel.reltype)
                    el.set(attr, new_rId)

            # Add the processed element to the target document before the paragraph
            para._element.addprevious(new_element)

    def _delete_paragraph(self, paragraph):
        """Deletes a paragraph from the document."""
        p = paragraph._element
        p.getparent().remove(p)
        paragraph._p = paragraph._element = None

    def _set_column_widths(self, table, widths):
        """Sets explicit column widths. Word (and python-docx) reads cell width
        ahead of column width, so both have to be set for it to stick."""
        table.autofit = False
        table.allow_autofit = False
        for row in table.rows:
            idx = 0
            while idx < len(widths):
                cell = row.cells[idx]
                # A horizontally merged cell reports the same underlying <w:tc>
                # for every column it spans, so give it the summed width of
                # those columns instead of overwriting it once per column
                # (which would leave it as narrow as the last column alone).
                span = 1
                while idx + span < len(widths) and row.cells[idx + span]._tc is cell._tc:
                    span += 1
                cell.width = sum(widths[idx:idx + span])
                idx += span
        for idx, width in enumerate(widths):
            table.columns[idx].width = width

    def _set_table_borders(self, table, size=4, color="999999"):
        """Adds a thin grid border around and inside the table. No 'Table Grid'
        style is defined in the shell template, so the borders are set directly."""
        borders = OxmlElement('w:tblBorders')
        for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
            edge_el = OxmlElement(f'w:{edge}')
            edge_el.set(qn('w:val'), 'single')
            edge_el.set(qn('w:sz'), str(size))
            edge_el.set(qn('w:space'), '0')
            edge_el.set(qn('w:color'), color)
            borders.append(edge_el)
        table._tbl.tblPr.append(borders)

if __name__ == "__main__":
    root = tk.Tk()
    app = ContractManager(root)
    root.mainloop()
