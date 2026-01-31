import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from docling.document_converter import DocumentConverter
import os
import sys
import csv
import tempfile

import fitz  # PyMuPDF
from PIL import Image

from openpyxl import Workbook
from openpyxl.styles import Font, Border, Side
from openpyxl.utils import get_column_letter

sys.stdout.reconfigure(encoding="utf-8")

# =========================================================
# PDF → IMAGE (SCANNED MODE)
# =========================================================

def pdf_to_images(pdf_path, dpi=300):
    doc = fitz.open(pdf_path)
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)

    temp_dir = tempfile.mkdtemp(prefix="docling_pages_")
    image_paths = []

    for i in range(len(doc)):
        page = doc[i]
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_path = os.path.join(temp_dir, f"page_{i+1}.png")
        pix.save(img_path)
        image_paths.append(img_path)

    return image_paths


def auto_rotate_image(image_path):
    try:
        img = Image.open(image_path)
        img = Image.Image.transpose(img, Image.Transpose.EXIF)
        img.save(image_path)
    except Exception:
        pass
    return image_path


# =========================================================
# MARKDOWN TABLE PARSER
# =========================================================

def parse_markdown_tables(markdown_text):
    tables = []
    lines = markdown_text.splitlines()

    current = []
    inside = False

    for line in lines:
        if "|" in line:
            inside = True
            current.append(line)
        else:
            if inside:
                tables.append(current)
                current = []
                inside = False

    if current:
        tables.append(current)

    parsed = []

    for table in tables:
        rows = []
        for r in table:
            cells = [c.strip() for c in r.strip("|").split("|")]
            if all(set(c) <= {"-", ":"} for c in cells):
                continue
            rows.append(cells)

        if len(rows) < 2:
            continue

        headers = rows[0]
        col_len = len(headers)

        data = []
        for r in rows[1:]:
            if len(r) < col_len:
                r += [""] * (col_len - len(r))
            data.append(r[:col_len])

        parsed.append((headers, data))

    return parsed


# =========================================================
# UI HELPERS
# =========================================================

def center_window(win, w=760, h=430):
    win.update_idletasks()
    x = (win.winfo_screenwidth() // 2) - (w // 2)
    y = (win.winfo_screenheight() // 2) - (h // 2)
    win.geometry(f"{w}x{h}+{x}+{y}")


def browse_file():
    path = filedialog.askopenfilename(
        title="Select PDF or Image",
        filetypes=[
            ("Supported Files", "*.pdf *.png *.jpg *.jpeg *.bmp *.tiff")
        ]
    )
    if path:
        input_var.set(path)


def browse_output():
    path = filedialog.askdirectory(title="Select Output Folder")
    if path:
        output_var.set(path)


# =========================================================
# CORE PROCESS
# =========================================================

def process_document():
    source = input_var.get()
    out_dir = output_var.get()
    fmt = format_var.get()
    scanned_mode = scanned_var.get()

    if not source or not out_dir:
        messagebox.showerror("Error", "Please select input file and output folder")
        return

    try:
        converter = DocumentConverter()
        ext = os.path.splitext(source)[1].lower()

        docs_to_process = []

        # ---------- SCANNED PDF MODE ----------
        if scanned_mode and ext == ".pdf":
            pages = pdf_to_images(source, dpi=300)
            for p in pages:
                docs_to_process.append(auto_rotate_image(p))

        else:
            docs_to_process.append(source)

        markdown_text = ""
        for doc in docs_to_process:
            result = converter.convert(doc)
            markdown_text += "\n\n" + result.document.export_to_markdown()

        base = os.path.splitext(os.path.basename(source))[0]
        generated_files = []

        tables = parse_markdown_tables(markdown_text)

        # =====================================================
        # EXCEL OUTPUT
        # =====================================================
        if fmt == "Excel (.xlsx)":
            wb = Workbook()
            ws = wb.active
            ws.title = "Sheet1"

            bold = Font(bold=True)
            border = Border(
                left=Side(style="thin"),
                right=Side(style="thin"),
                top=Side(style="thin"),
                bottom=Side(style="thin")
            )

            lines = markdown_text.splitlines()
            table_idx = 0
            row_ptr = 1
            i = 0
            col_widths = {}

            while i < len(lines):
                line = lines[i]

                if "|" in line and table_idx < len(tables):
                    headers, rows = tables[table_idx]
                    cols = len(headers)

                    for c, h in enumerate(headers, 1):
                        cell = ws.cell(row=row_ptr, column=c, value=h)
                        cell.font = bold
                        cell.border = border
                        col_widths[c] = max(col_widths.get(c, 0), len(str(h)))

                    row_ptr += 1

                    for r in rows:
                        for c in range(cols):
                            val = r[c]
                            cell = ws.cell(row=row_ptr, column=c+1, value=val)
                            cell.border = border
                            col_widths[c+1] = max(col_widths.get(c+1, 0), len(str(val)))
                        row_ptr += 1

                    table_idx += 1
                    while i < len(lines) and "|" in lines[i]:
                        i += 1

                    row_ptr += 1
                    continue

                ws.cell(row=row_ptr, column=1, value=line)
                col_widths[1] = max(col_widths.get(1, 0), len(line))
                row_ptr += 1
                i += 1

            for c, w in col_widths.items():
                ws.column_dimensions[get_column_letter(c)].width = min(w + 3, 60)

            out_path = os.path.join(out_dir, base + ".xlsx")
            wb.save(out_path)
            generated_files.append(out_path)

        # =====================================================
        # MARKDOWN / TXT / CSV (UNCHANGED)
        # =====================================================
        else:
            ext_map = {
                "Markdown (.md)": ".md",
                "Text (.txt)": ".txt",
                "CSV (.csv)": ".csv"
            }
            out_path = os.path.join(out_dir, base + ext_map[fmt])
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(markdown_text)
            generated_files.append(out_path)

        messagebox.showinfo(
            "Completed",
            "File generated successfully:\n\n" + "\n".join(generated_files)
        )

    except Exception as e:
        messagebox.showerror("Processing Failed", str(e))


# =========================================================
# UI
# =========================================================

root = tk.Tk()
root.title("Document Conversion Tool")
root.resizable(False, False)
center_window(root)

# Focus window
root.update_idletasks()
root.attributes("-topmost", True)
root.focus_force()
root.after(300, lambda: root.attributes("-topmost", False))

BG = "#F4F4F4"
FG = "#161616"
BTN = "#0F62FE"

root.configure(bg=BG)

style = ttk.Style()
style.theme_use("clam")
style.configure("TLabel", background=BG, foreground=FG)
style.configure("TEntry", foreground=FG)
style.configure("TButton", background=BTN, foreground="white", font=("Segoe UI", 10, "bold"))

input_var = tk.StringVar()
output_var = tk.StringVar()
format_var = tk.StringVar(value="Excel (.xlsx)")
scanned_var = tk.BooleanVar(value=False)

frame = tk.Frame(root, bg=BG)
frame.pack(padx=30, pady=25)

ttk.Label(frame, text="Input PDF / Image").grid(row=0, column=0, sticky="w")
ttk.Entry(frame, textvariable=input_var, width=70).grid(row=1, column=0, padx=(0, 10))
ttk.Button(frame, text="Browse", command=browse_file).grid(row=1, column=1)

ttk.Label(frame, text="Output Folder").grid(row=2, column=0, sticky="w", pady=10)
ttk.Entry(frame, textvariable=output_var, width=70).grid(row=3, column=0, padx=(0, 10))
ttk.Button(frame, text="Browse", command=browse_output).grid(row=3, column=1)

ttk.Label(frame, text="Output Format").grid(row=4, column=0, sticky="w", pady=10)
ttk.Combobox(
    frame,
    textvariable=format_var,
    values=["Excel (.xlsx)", "Markdown (.md)", "Text (.txt)", "CSV (.csv)"],
    state="readonly",
    width=22
).grid(row=5, column=0, sticky="w")

ttk.Checkbutton(
    frame,
    text="Scanned PDF Mode (High-Resolution OCR)",
    variable=scanned_var
).grid(row=6, column=0, sticky="w", pady=10)

ttk.Button(frame, text="Convert Document", command=process_document)\
    .grid(row=7, column=0, pady=25)

root.mainloop()
