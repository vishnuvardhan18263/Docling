import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk
import fitz  # PyMuPDF
import pandas as pd  # Required for Excel export
from docling.document_converter import DocumentConverter

# Supported extensions for INPUT files
SUPPORTED_EXT = (".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff")

class DoclingOCRApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Docling OCR Utility")
        self.root.geometry("1200x720")
        self.root.focus_force()
        
        # Data containers
        self.selected_file = None
        self.output_folder = None  # Renamed to clarify it is for OUTPUT
        self.original_image = None
        self.preview_image = None
        
        self.build_ui()

    # ---------------- UI ---------------- #
    def build_ui(self):
        top = tk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=5)
        
        # Buttons
        tk.Button(top, text="Select Input File (PDF/Img)", command=self.pick_file).pack(side="left")
        tk.Button(top, text="Select Output Folder", command=self.pick_output_folder).pack(side="left", padx=10)
        
        # Labels area
        labels = tk.Frame(self.root)
        labels.pack(fill="x", padx=10)
        
        self.file_label = tk.Label(labels, text="Input File: None", anchor="w", fg="blue")
        self.file_label.pack(fill="x")
        
        self.folder_label = tk.Label(labels, text="Output Folder: [Same as Input]", anchor="w", fg="green")
        self.folder_label.pack(fill="x")
        
        # Main body (Canvas + Sidebar)
        body = tk.Frame(self.root)
        body.pack(fill="both", expand=True)
        
        self.canvas = tk.Canvas(body, bg="#eeeeee")
        self.canvas.pack(side="left", fill="both", expand=True)
        
        side = tk.Frame(body, width=260)
        side.pack(side="right", fill="y", padx=10)
        
        # Sidebar Controls
        tk.Label(side, text="Rotation (Preview Only)").pack(anchor="w", pady=(10, 0))
        self.rotation = tk.StringVar(value="0")
        rot = ttk.Combobox(side, textvariable=self.rotation, 
                           values=["0", "90", "180", "270"], state="readonly")
        rot.pack(fill="x")
        rot.bind("<<ComboboxSelected>>", lambda e: self.update_preview())
        
        tk.Label(side, text="Output Format").pack(anchor="w", pady=(15, 0))
        self.output_fmt = tk.StringVar(value="xlsx")
        ttk.Combobox(side, textvariable=self.output_fmt, 
                     values=["xlsx", "txt"], state="readonly").pack(fill="x")
        
        self.start_btn = tk.Button(side, text="Start OCR", height=2, command=self.start_thread)
        self.start_btn.pack(fill="x", pady=20)
        
        self.progress = ttk.Progressbar(side, mode="indeterminate")
        self.progress.pack(fill="x")

    # ---------------- FILE & FOLDER PICKING ---------------- #
    def pick_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("PDF / Images", "*.pdf *.png *.jpg *.jpeg *.bmp *.tiff")]
        )
        if path:
            self.selected_file = path
            self.file_label.config(text=f"Input File: {path}")
            self.load_preview(path)
            # We do NOT clear the output folder here, so previous selection remains valid.

    def pick_output_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.output_folder = path
            self.folder_label.config(text=f"Output Folder: {path}")
            # We do NOT clear the input file or preview here.
            # This ensures no conflict between input and output selection.

    # ---------------- PREVIEW ---------------- #
    def load_preview(self, path):
        try:
            if path.lower().endswith(".pdf"):
                doc = fitz.open(path)
                page = doc[0]
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            else:
                img = Image.open(path)
                
            self.original_image = img
            self.update_preview()
        except Exception as e:
            messagebox.showerror("Preview Error", str(e))

    def update_preview(self):
        if not self.original_image:
            return
        img = self.original_image.copy()
        angle = int(self.rotation.get())
        if angle:
            img = img.rotate(-angle, expand=True)
            
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        
        if w <= 1 or h <= 1:
            w, h = 400, 400
            
        img.thumbnail((w, h))
        self.preview_image = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(w // 2, h // 2, image=self.preview_image, anchor="center")

    # ---------------- OCR PROCESS ---------------- #
    def start_thread(self):
        if not self.selected_file:
            messagebox.showwarning("Missing Input", "Please select an input file.")
            return
        
        self.progress.start()
        self.start_btn.config(state="disabled")
        threading.Thread(target=self.run_ocr, daemon=True).start()

    def run_ocr(self):
        try:
            converter = DocumentConverter()
            
            # Use the selected file
            f = self.selected_file
            
            # Determine output path
            # If output_folder is selected, use it. Otherwise, use input file's directory.
            if self.output_folder:
                out_dir = self.output_folder
            else:
                out_dir = os.path.dirname(f)
            
            # Prepare filenames
            base_name = os.path.splitext(os.path.basename(f))[0]
            
            # Perform Conversion
            result = converter.convert(f)
            
            # Handle Text Output
            if self.output_fmt.get() == "txt":
                out_path = os.path.join(out_dir, base_name + ".txt")
                with open(out_path, "w", encoding="utf-8") as out:
                    out.write(result.document.export_to_markdown())
            
            # Handle Excel Output
            else:
                out_path = os.path.join(out_dir, base_name + ".xlsx")
                tables_found = False
                with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
                    # 1. Export Tables
                    for i, table in enumerate(result.document.tables):
                        df = table.export_to_dataframe()
                        sheet_name = f"Table_{i+1}"
                        df.to_excel(writer, sheet_name=sheet_name, index=False)
                        tables_found = True
                    
                    # 2. Export Full Text if no tables found (or as backup)
                    if not tables_found:
                        full_text = result.document.export_to_markdown()
                        df_text = pd.DataFrame({"Document Content": [full_text]})
                        df_text.to_excel(writer, sheet_name="Full Text", index=False)

            self.root.after(0, lambda: messagebox.showinfo("Done", f"Saved to:\n{out_path}"))
        
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("OCR Error", str(e)))
        finally:
            self.root.after(0, self.stop_progress)

    def stop_progress(self):
        self.progress.stop()
        self.start_btn.config(state="normal")

if __name__ == "__main__":
    root = tk.Tk()
    app = DoclingOCRApp(root)
    root.mainloop()