import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from docling.document_converter import DocumentConverter
import os
import sys
import csv
from openpyxl import Workbook

sys.stdout.reconfigure(encoding="utf-8")

# ------------------ TABLE PARSER ------------------

def parse_markdown_tables(markdown_text):
    tables = []
    lines = markdown_text.splitlines()

    current_table = []
    inside_table = False

    for line in lines:
        if "|" in line:
            inside_table = True
            current_table.append(line)
        else:
            if inside_table:
                tables.append(current_table)
                current_table = []
                inside_table = False

    if current_table:
        tables.append(current_table)

    parsed_tables = []

    for table in tables:
        rows = []
        for row in table:
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            # Skip separator rows like |----|----|
            if all(set(c) <= {"-", ":"} for c in cells):
                continue
            rows.append(cells)

        if len(rows) >= 2:
            headers = rows[0]
            data_rows = rows[1:]
            parsed_tables.append((headers, data_rows))

    return parsed_tables


# ------------------ UI HELPERS ------------------

def center_window(win, width=720, height=380):
    win.update_idletasks()
    x = (win.winfo_screenwidth() // 2) - (width // 2)
    y = (win.winfo_screenheight() // 2) - (height // 2)
    win.geometry(f"{width}x{height}+{x}+{y}")

def browse_file():
    file_path = filedialog.askopenfilename(
        title="Select Invoice PDF",
        filetypes=[("PDF Files", "*.pdf")]
    )
    if file_path:
        input_var.set(file_path)

def browse_output():
    folder_path = filedialog.askdirectory(title="Select Output Folder")
    if folder_path:
        output_var.set(folder_path)


# ------------------ PROCESS ------------------

def process_document():
    source = input_var.get()
    output_dir = output_var.get()
    fmt = format_var.get()

    if not source or not output_dir:
        messagebox.showerror("Error", "Please select input file and output folder")
        return

    try:
        converter = DocumentConverter()
        result = converter.convert(source)
        markdown_text = result.document.export_to_markdown()

        base_name = os.path.splitext(os.path.basename(source))[0]

        # ------------------ MARKDOWN / TXT ------------------
        if fmt in ("Markdown (.md)", "Text (.txt)"):
            ext = ".md" if "Markdown" in fmt else ".txt"
            out_path = os.path.join(output_dir, base_name + ext)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(markdown_text)

        # ------------------ CSV ------------------
        elif fmt == "CSV (.csv)":
            tables = parse_markdown_tables(markdown_text)

            if not tables:
                raise ValueError("No tables found in document")

            for idx, (headers, rows) in enumerate(tables, start=1):
                csv_path = os.path.join(
                    output_dir, f"{base_name}_table_{idx}.csv"
                )
                with open(csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(headers)
                    writer.writerows(rows)

        # ------------------ EXCEL ------------------
        elif fmt == "Excel (.xlsx)":
            tables = parse_markdown_tables(markdown_text)

            if not tables:
                raise ValueError("No tables found in document")

            wb = Workbook()
            wb.remove(wb.active)

            for idx, (headers, rows) in enumerate(tables, start=1):
                ws = wb.create_sheet(title=f"Table_{idx}")
                ws.append(headers)
                for r in rows:
                    ws.append(r)

            xlsx_path = os.path.join(output_dir, base_name + ".xlsx")
            wb.save(xlsx_path)

        messagebox.showinfo("Success", "Invoice processed successfully")

    except Exception as e:
        messagebox.showerror("Processing Failed", str(e))


# ------------------ MAIN UI ------------------

root = tk.Tk()
root.title("Docling Invoice Converter")
root.resizable(False, False)
center_window(root)

# IBM Carbon Light Theme
BG = "#F4F4F4"
FG = "#161616"
BTN_BG = "#0F62FE"
BTN_ACTIVE = "#0353E9"
BTN_FG = "#FFFFFF"
ENTRY_BG = "#FFFFFF"

root.configure(bg=BG)

style = ttk.Style()
style.theme_use("clam")

style.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
style.configure("TEntry", fieldbackground=ENTRY_BG, foreground=FG)
style.configure(
    "TButton",
    background=BTN_BG,
    foreground=BTN_FG,
    font=("Segoe UI", 10, "bold"),
    padding=8
)
style.map("TButton", background=[("active", BTN_ACTIVE)])

# ------------------ VARIABLES ------------------

input_var = tk.StringVar()
output_var = tk.StringVar()
format_var = tk.StringVar(value="Markdown (.md)")

# ------------------ LAYOUT ------------------

frame = tk.Frame(root, bg=BG)
frame.pack(expand=True, fill="both", padx=30, pady=25)

ttk.Label(frame, text="Input Invoice PDF").grid(row=0, column=0, sticky="w")
ttk.Entry(frame, textvariable=input_var, width=55).grid(row=1, column=0, padx=(0, 10))
ttk.Button(frame, text="Browse", command=browse_file).grid(row=1, column=1)

ttk.Label(frame, text="Output Folder").grid(row=2, column=0, sticky="w", pady=10)
ttk.Entry(frame, textvariable=output_var, width=55).grid(row=3, column=0, padx=(0, 10))
ttk.Button(frame, text="Browse", command=browse_output).grid(row=3, column=1)

ttk.Label(frame, text="Output Format").grid(row=4, column=0, sticky="w", pady=10)
ttk.Combobox(
    frame,
    textvariable=format_var,
    values=["Markdown (.md)", "Text (.txt)", "CSV (.csv)", "Excel (.xlsx)"],
    state="readonly",
    width=22
).grid(row=5, column=0, sticky="w")

ttk.Button(
    frame,
    text="Convert Invoice",
    command=process_document
).grid(row=6, column=0, pady=25)

root.mainloop()
