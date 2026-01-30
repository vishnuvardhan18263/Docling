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
        raw_rows = []
        for row in table:
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            # skip separator rows like |----|----|
            if all(set(c) <= {"-", ":"} for c in cells):
                continue
            raw_rows.append(cells)

        if len(raw_rows) < 2:
            continue

        headers = raw_rows[0]
        col_count = len(headers)

        normalized_rows = []
        for r in raw_rows[1:]:
            if len(r) < col_count:
                r = r + [""] * (col_count - len(r))
            elif len(r) > col_count:
                r = r[:col_count]
            normalized_rows.append(r)

        parsed_tables.append((headers, normalized_rows))

    return parsed_tables


# ------------------ UI HELPERS ------------------

def center_window(win, width=720, height=380):
    win.update_idletasks()
    x = (win.winfo_screenwidth() // 2) - (width // 2)
    y = (win.winfo_screenheight() // 2) - (height // 2)
    win.geometry(f"{width}x{height}+{x}+{y}")

def browse_file():
    path = filedialog.askopenfilename(
        title="Select Invoice PDF",
        filetypes=[("PDF Files", "*.pdf")]
    )
    if path:
        input_var.set(path)

def browse_output():
    path = filedialog.askdirectory(title="Select Output Folder")
    if path:
        output_var.set(path)


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

        base = os.path.splitext(os.path.basename(source))[0]
        generated_files = []

        tables = parse_markdown_tables(markdown_text)

        # ------------------ MARKDOWN / TXT ------------------
        if fmt in ("Markdown (.md)", "Text (.txt)"):
            ext = ".md" if "Markdown" in fmt else ".txt"
            out_path = os.path.join(output_dir, base + ext)

            with open(out_path, "w", encoding="utf-8") as f:
                f.write(markdown_text)
                for i, (headers, rows) in enumerate(tables, 1):
                    f.write(f"\n\n--- TABLE {i} ---\n")
                    f.write(" | ".join(headers) + "\n")
                    for r in rows:
                        f.write(" | ".join(r) + "\n")

            generated_files.append(out_path)

        # ------------------ CSV ------------------
        elif fmt == "CSV (.csv)":
            header_path = os.path.join(output_dir, base + "_header.txt")
            with open(header_path, "w", encoding="utf-8") as f:
                f.write(markdown_text)
            generated_files.append(header_path)

            for i, (headers, rows) in enumerate(tables, 1):
                csv_path = os.path.join(output_dir, f"{base}_table_{i}.csv")
                with open(csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(headers)
                    writer.writerows(rows)
                generated_files.append(csv_path)

        # ------------------ EXCEL ------------------
        elif fmt == "Excel (.xlsx)":
            wb = Workbook()
            ws = wb.active
            ws.title = "Sheet1"

            lines = markdown_text.splitlines()
            tables = parse_markdown_tables(markdown_text)

            table_idx = 0
            row_ptr = 1
            i = 0

            while i < len(lines):
                line = lines[i]

                # -------- TABLE DETECTED --------
                if "|" in line and table_idx < len(tables):
                    headers, rows = tables[table_idx]
                    col_count = len(headers)

                    # write headers
                    for col, h in enumerate(headers, start=1):
                        ws.cell(row=row_ptr, column=col, value=h)
                    row_ptr += 1

                    # write rows
                    for r in rows:
                        for col in range(col_count):
                            ws.cell(
                                row=row_ptr,
                                column=col + 1,
                                value=r[col] if col < len(r) else ""
                            )
                        row_ptr += 1

                    table_idx += 1

                    # skip original markdown table lines
                    while i < len(lines) and "|" in lines[i]:
                        i += 1

                    row_ptr += 1  # blank row after table
                    continue

                # -------- NORMAL TEXT --------
                ws.cell(row=row_ptr, column=1, value=line)
                row_ptr += 1
                i += 1

            # ✅ SAVE ONCE
            xlsx_path = os.path.join(output_dir, base + ".xlsx")
            wb.save(xlsx_path)
            generated_files.append(xlsx_path)

            messagebox.showinfo(
                "Completed",
                "Files generated successfully:\n\n" + "\n".join(generated_files)
            )

    except Exception as e:
        messagebox.showerror("Processing Failed", str(e))


# ------------------ UI ------------------

root = tk.Tk()
root.title("Docling Invoice Converter")
root.resizable(False, False)
center_window(root)

BG = "#F4F4F4"
FG = "#161616"
BTN_BG = "#0F62FE"
BTN_ACTIVE = "#0353E9"
BTN_FG = "#FFFFFF"

root.configure(bg=BG)

style = ttk.Style()
style.theme_use("clam")

style.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
style.configure("TEntry", fieldbackground="#FFFFFF", foreground=FG)
style.configure(
    "TButton",
    background=BTN_BG,
    foreground=BTN_FG,
    font=("Segoe UI", 10, "bold"),
    padding=8
)
style.map("TButton", background=[("active", BTN_ACTIVE)])

input_var = tk.StringVar()
output_var = tk.StringVar()
format_var = tk.StringVar(value="Markdown (.md)")

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

ttk.Button(frame, text="Convert Invoice", command=process_document)\
    .grid(row=6, column=0, pady=25)

root.mainloop()
