import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from docling.document_converter import DocumentConverter
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

# ------------------ UI HELPERS ------------------

def center_window(win, width=700, height=340):
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

def process_document():
    source = input_var.get()
    output_dir = output_var.get()

    if not source or not output_dir:
        messagebox.showerror("Error", "Please select both input file and output folder")
        return

    try:
        converter = DocumentConverter()
        result = converter.convert(source)

        output_file = os.path.join(
            output_dir,
            os.path.splitext(os.path.basename(source))[0] + ".md"
        )

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(result.document.export_to_markdown())

        messagebox.showinfo("Success", f"File generated:\n{output_file}")

    except Exception as e:
        messagebox.showerror("Processing Failed", str(e))


# ------------------ MAIN UI ------------------

root = tk.Tk()
root.title("Docling Invoice Converter")
root.resizable(False, False)
center_window(root)

# IBM Carbon (Light Theme)
BG = "#F4F4F4"
FG = "#161616"
BTN_BG = "#0F62FE"
BTN_ACTIVE = "#0353E9"
BTN_FG = "#FFFFFF"
ENTRY_BG = "#FFFFFF"
BORDER = "#C6C6C6"

root.configure(bg=BG)

style = ttk.Style()
style.theme_use("clam")

style.configure(
    "TLabel",
    background=BG,
    foreground=FG,
    font=("Segoe UI", 10)
)

style.configure(
    "TEntry",
    fieldbackground=ENTRY_BG,
    foreground=FG,
    bordercolor=BORDER,
    lightcolor=BORDER,
    darkcolor=BORDER
)

style.configure(
    "TButton",
    background=BTN_BG,
    foreground=BTN_FG,
    padding=8,
    font=("Segoe UI", 10, "bold"),
    borderwidth=0
)

style.map(
    "TButton",
    background=[("active", BTN_ACTIVE)]
)

# ------------------ VARIABLES ------------------

input_var = tk.StringVar()
output_var = tk.StringVar()

# ------------------ LAYOUT ------------------

frame = tk.Frame(root, bg=BG)
frame.pack(expand=True, fill="both", padx=30, pady=30)

ttk.Label(frame, text="Input Invoice PDF").grid(row=0, column=0, sticky="w", pady=8)
ttk.Entry(frame, textvariable=input_var, width=55).grid(row=1, column=0, padx=(0, 10))
ttk.Button(frame, text="Browse", command=browse_file).grid(row=1, column=1)

ttk.Label(frame, text="Output Folder").grid(row=2, column=0, sticky="w", pady=15)
ttk.Entry(frame, textvariable=output_var, width=55).grid(row=3, column=0, padx=(0, 10))
ttk.Button(frame, text="Browse", command=browse_output).grid(row=3, column=1)

ttk.Button(
    frame,
    text="Convert Invoice",
    command=process_document
).grid(row=5, column=0, pady=30)

root.mainloop()
