import os
import sys
import shutil
import tempfile
from pathlib import Path
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import fitz  # PyMuPDF
from PIL import Image, ImageTk
import pandas as pd

from docling.document_converter import DocumentConverter

# Ensure UTF-8 console on Windows (best effort)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
PDF_EXTS = {".pdf"}

OUTPUT_FORMATS = [
    "Excel (.xlsx)",
    "Markdown (.md)",
    "Plain Text (.txt)",
    "CSV (.csv)",
]


class CombinedResult:
    """Holds combined multi-page OCR results (texts + tables)."""
    def __init__(self, texts=None, tables=None):
        self.texts = texts or []
        self.tables = tables or []


class DoclingOCRTool:
    def __init__(self, root):
        self.root = root
        self.root.title("Docling OCR Utility")
        self.root.geometry("1200x760")

        # State
        self.input_file: Path | None = None
        self.output_folder: Path | None = None

        # UI variables
        self.rotation = tk.StringVar(value="0")        # rotation for preview, images, and (PDF→image) path
        self.ocr_dpi = tk.StringVar(value="300")       # target DPI for images & scanned PDFs
        self.is_scanned = tk.BooleanVar(value=False)   # scanned PDF indicator
        self.use_grayscale = tk.BooleanVar(value=False)  # grayscale during PDF rendering only
        self.output_format = tk.StringVar(value=OUTPUT_FORMATS[0])  # NEW: output format

        # Preview
        self.original_image = None
        self.preview_image = None

        self.build_ui()

    # ---------------- UI ---------------- #

    def build_ui(self):
        top = tk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=5)

        tk.Button(top, text="Select Input File", command=self.pick_input).pack(side="left")
        tk.Button(top, text="Select Output Folder", command=self.pick_output).pack(side="left", padx=5)

        self.lbl_input = tk.Label(self.root, fg="blue", anchor="w")
        self.lbl_input.pack(fill="x", padx=10)

        self.lbl_output = tk.Label(self.root, fg="green", anchor="w")
        self.lbl_output.pack(fill="x", padx=10)

        body = tk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=10, pady=10)

        preview_frame = tk.LabelFrame(body, text="Preview")
        preview_frame.pack(side="left", fill="both", expand=True)

        self.canvas = tk.Canvas(preview_frame, bg="#eeeeee")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.update_preview())

        side = tk.Frame(body, width=320)
        side.pack(side="right", fill="y", padx=10)

        # Rotation
        tk.Label(side, text="Rotation (Preview & OCR)").pack(anchor="w")
        rot = ttk.Combobox(
            side,
            textvariable=self.rotation,
            values=["0", "90", "180", "270"],
            state="readonly"
        )
        rot.pack(fill="x")
        rot.bind("<<ComboboxSelected>>", lambda e: self.update_preview())

        # DPI
        tk.Label(side, text="Target DPI (Images & Scanned PDFs)").pack(anchor="w", pady=(10, 0))
        dpi_box = ttk.Combobox(
            side,
            textvariable=self.ocr_dpi,
            values=["200", "300", "400", "600"],
            state="readonly"
        )
        dpi_box.pack(fill="x")

        # Scanned PDF toggle
        tk.Checkbutton(
            side,
            text="Scanned PDF (Image-based OCR)",
            variable=self.is_scanned
        ).pack(anchor="w", pady=(15, 0))

        # Grayscale (PDF rendering only)
        tk.Checkbutton(
            side,
            text="Save Grayscale (PDF rendering only)",
            variable=self.use_grayscale
        ).pack(anchor="w", pady=(4, 10))

        # Output format dropdown (NEW)
        tk.Label(side, text="Output Format").pack(anchor="w", pady=(8, 0))
        fmt_box = ttk.Combobox(
            side,
            textvariable=self.output_format,
            values=OUTPUT_FORMATS,
            state="readonly"
        )
        fmt_box.pack(fill="x")

        self.start_btn = tk.Button(
            side,
            text="Start OCR",
            height=2,
            command=self.start_ocr
        )
        self.start_btn.pack(fill="x", pady=14)

        self.progress = ttk.Progressbar(side, mode="indeterminate")
        self.progress.pack(fill="x")

        # Hint
        tk.Label(
            side,
            text=(
                "Images: apply Rotation + DPI (with upscaling if needed), then send to OCR.\n"
                "Scanned PDFs: render pages at target DPI.\n"
                "Digital PDFs: processed directly.\n"
                "CSV may produce multiple files (text + tables)."
            ),
            foreground="#444",
            justify="left",
            wraplength=300
        ).pack(anchor="w", pady=8)

    # ---------------- File Selection ---------------- #

    def pick_input(self):
        path = filedialog.askopenfilename(
            filetypes=[("PDF / Images", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff")]
        )
        if path:
            self.input_file = Path(path)
            self.lbl_input.config(text=f"Input File: {path}")
            self.load_preview()

    def pick_output(self):
        path = filedialog.askdirectory()
        if path:
            self.output_folder = Path(path)
            self.lbl_output.config(text=f"Output Folder: {path}")

    # ---------------- Preview ---------------- #

    def load_preview(self):
        """Load first page / image for UI preview (fast rendering)."""
        try:
            if self.input_file.suffix.lower() in PDF_EXTS:
                doc = fitz.open(self.input_file)
                page = doc[0]
                # ~180 DPI preview (not used for OCR)
                pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            else:
                img = Image.open(self.input_file)

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

        w = self.canvas.winfo_width() or 800
        h = self.canvas.winfo_height() or 600
        img.thumbnail((w, h))
        self.preview_image = ImageTk.PhotoImage(img)

        self.canvas.delete("all")
        self.canvas.create_image(w // 2, h // 2, image=self.preview_image, anchor="center")

    # ---------------- OCR ---------------- #

    def start_ocr(self):
        if not self.input_file or not self.output_folder:
            messagebox.showerror("Missing Input", "Select input file and output folder")
            return

        self.progress.start()
        self.start_btn.config(state="disabled")
        self.root.after(100, self.run_ocr)

    def run_ocr(self):
        temp_paths = []   # collect temp dirs for cleanup
        created_files = []
        try:
            converter = DocumentConverter()
            suffix = self.input_file.suffix.lower()

            # 1) Images: apply ONLY rotation + DPI, then OCR
            if suffix in IMAGE_EXTS:
                temp_dir, prepped_image = self.prepare_image_temp(self.input_file)
                temp_paths.append(temp_dir)
                result = converter.convert(str(prepped_image))
                created_files = self.export_by_selected_format(result)

            # 2) Scanned PDFs: render at high DPI to TIFF, OCR each
            elif suffix in PDF_EXTS and self.is_scanned.get():
                temp_dir = self.pdf_to_tiff_temp(self.input_file)
                temp_paths.append(temp_dir)

                texts, tables = [], []
                image_files = sorted(temp_dir.glob("*.tiff"))
                if not image_files:
                    raise RuntimeError("No TIFF images generated for OCR")

                for img_path in image_files:
                    r = converter.convert(str(img_path))
                    texts.append(r.document.export_to_markdown())
                    if r.document.tables:
                        tables.extend(r.document.tables)

                combined_result = CombinedResult(texts=texts, tables=tables)
                created_files = self.export_by_selected_format(combined_result)

            # 3) Digital PDFs: let Docling handle directly
            else:
                result = converter.convert(str(self.input_file))
                created_files = self.export_by_selected_format(result)

            # Build message
            file_list = "\n".join(str(p) for p in created_files)
            messagebox.showinfo(
                "Completed",
                f"OCR Completed Successfully!\n\n"
                f"Input File:\n{self.input_file}\n\n"
                f"Generated File(s):\n{file_list}"
            )

        except Exception as err:
            messagebox.showerror("OCR Error", str(err))

        finally:
            for p in temp_paths:
                try:
                    if p and p.exists():
                        shutil.rmtree(p, ignore_errors=True)
                except Exception:
                    pass
            self.progress.stop()
            self.start_btn.config(state="normal")

    # ---------------- Image prep: Rotation + DPI ---------------- #

    def prepare_image_temp(self, image_path: Path):
        """
        Apply ONLY rotation and target DPI to an image.
        - If image lacks DPI, assume 72 and upscale pixels to target DPI.
        - If DPI < target, upscale (LANCZOS) so OCR gets more pixels.
        - Save as TIFF (LZW) with embedded DPI.
        Returns (temp_dir, prepared_image_path).
        """
        img = Image.open(image_path)

        # Normalize mode
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # Rotation
        angle = int(self.rotation.get())
        if angle:
            img = img.rotate(-angle, expand=True)

        # Target DPI
        try:
            target_dpi = int(self.ocr_dpi.get())
        except Exception:
            target_dpi = 300

        # Current DPI
        cur_dpi = img.info.get("dpi", (72, 72))
        if isinstance(cur_dpi, (list, tuple)) and len(cur_dpi) == 2:
            xdpi, ydpi = cur_dpi
        else:
            xdpi = ydpi = 72

        try:
            xdpi = float(xdpi) if xdpi else 72.0
            ydpi = float(ydpi) if ydpi else 72.0
        except Exception:
            xdpi = ydpi = 72.0

        base_dpi = min(xdpi, ydpi) if min(xdpi, ydpi) > 0 else 72.0

        # Upscale pixels if target DPI > current
        scale = target_dpi / base_dpi
        if scale > 1.01:
            new_w = int(round(img.width * scale))
            new_h = int(round(img.height * scale))
            img = img.resize((new_w, new_h), Image.LANCZOS)

        # Save to temp TIFF with embedded DPI
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        temp_dir = Path(tempfile.gettempdir()) / f"OCR_IMG_{timestamp}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        out_path = temp_dir / f"{image_path.stem}_prepped.tiff"

        img.save(
            out_path,
            format="TIFF",
            compression="tiff_lzw",
            dpi=(target_dpi, target_dpi),
        )

        return temp_dir, out_path

    # ---------------- PDF → TIFF (High‑DPI) ---------------- #

    def pdf_to_tiff_temp(self, pdf_path: Path) -> Path:
        """Render each page of a scanned PDF to high‑DPI, lossless TIFFs."""
        doc = fitz.open(pdf_path)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_dir = Path(tempfile.gettempdir()) / f"OCR_{timestamp}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            dpi = int(self.ocr_dpi.get())
        except Exception:
            dpi = 300

        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)

        angle = int(self.rotation.get())
        to_gray = self.use_grayscale.get()

        for i, page in enumerate(doc):
            try:
                pix = page.get_pixmap(dpi=dpi, alpha=False)  # PyMuPDF >= 1.23
            except TypeError:
                pix = page.get_pixmap(matrix=matrix, alpha=False)

            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            if angle:
                img = img.rotate(-angle, expand=True)

            if to_gray:
                img = img.convert("L")

            img.save(
                temp_dir / f"page_{i+1:03}.tiff",
                format="TIFF",
                compression="tiff_lzw",
                dpi=(dpi, dpi),
            )

        return temp_dir

    # ---------------- Export Routing (by dropdown) ---------------- #

    def export_by_selected_format(self, result):
        fmt = self.output_format.get()
        if fmt.startswith("Excel"):
            return [self.export_to_excel(result)]
        elif fmt.startswith("Markdown"):
            return [self.export_to_markdown(result)]
        elif fmt.startswith("Plain Text"):
            return [self.export_to_text(result)]
        elif fmt.startswith("CSV"):
            return self.export_to_csv(result)
        else:
            # Fallback to Excel
            return [self.export_to_excel(result)]

    # ---------------- Helpers: Formatting ---------------- #

    @staticmethod
    def _df_to_markdown(df: pd.DataFrame) -> str:
        """
        Render a pandas DataFrame as GitHub-style pipe table WITHOUT extra deps.
        Escapes pipes within cell content.
        """
        def esc(x: str) -> str:
            return ("" if x is None else str(x)).replace("|", "\\|")

        cols = [esc(c) for c in df.columns]
        header = "| " + " | ".join(cols) + " |\n"
        align = "| " + " | ".join(["---"] * len(cols)) + " |\n"

        rows = []
        for _, row in df.iterrows():
            cells = [esc(v) for v in row.tolist()]
            rows.append("| " + " | ".join(cells) + " |")
        return header + align + "\n".join(rows)

    # ---------------- Exporters ---------------- #

    def _default_output_dir(self) -> Path:
        return self.output_folder if (self.output_folder and self.output_folder.exists()) else (Path.home() / "Documents")

    def export_to_excel(self, result):
        """Export result to a single Excel .xlsx file (returns Path)."""
        output_dir = self._default_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{self.input_file.stem}.xlsx"

        # CombinedResult (multi-page)
        if isinstance(result, CombinedResult):
            with pd.ExcelWriter(out_file, engine="openpyxl") as writer:
                if result.tables:
                    for i, table in enumerate(result.tables):
                        df = table.export_to_dataframe()
                        df.to_excel(writer, sheet_name=f"Table_{i+1}", index=False)

                all_text = "\n\n".join(result.texts) if result.texts else ""
                pd.DataFrame({"Document Text": [all_text]}).to_excel(writer, sheet_name="Text", index=False)

            return out_file

        # Single document
        if result.document.tables:
            with pd.ExcelWriter(out_file, engine="openpyxl") as writer:
                for i, table in enumerate(result.document.tables):
                    df = table.export_to_dataframe()
                    df.to_excel(writer, sheet_name=f"Table_{i+1}", index=False)
                text = result.document.export_to_markdown()
                pd.DataFrame({"Document Text": [text]}).to_excel(writer, sheet_name="Text", index=False)
        else:
            text = result.document.export_to_markdown()
            pd.DataFrame({"Document Text": [text]}).to_excel(out_file, index=False)

        return out_file

    def export_to_markdown(self, result):
        """Export result to a single Markdown .md file (returns Path)."""
        output_dir = self._default_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{self.input_file.stem}.md"

        parts = [f"# {self.input_file.stem}\n"]

        if isinstance(result, CombinedResult):
            if result.texts:
                parts.append("\n".join(result.texts))
            if result.tables:
                for i, table in enumerate(result.tables, start=1):
                    df = table.export_to_dataframe()
                    parts.append(f"\n\n## Table {i}\n")
                    parts.append(self._df_to_markdown(df))
        else:
            # Single document
            parts.append(result.document.export_to_markdown())
            if result.document.tables:
                for i, table in enumerate(result.document.tables, start=1):
                    df = table.export_to_dataframe()
                    parts.append(f"\n\n## Table {i}\n")
                    parts.append(self._df_to_markdown(df))

        out_file.write_text("\n".join(parts), encoding="utf-8")
        return out_file

    def export_to_text(self, result):
        """
        Export result to a single .txt file.
        We preserve structure by writing the same Markdown‑styled content
        (works well in plain text viewers).
        """
        output_dir = self._default_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{self.input_file.stem}.txt"

        parts = [f"{self.input_file.stem}\n", "=" * len(self.input_file.stem), "\n"]

        if isinstance(result, CombinedResult):
            if result.texts:
                parts.append("\n".join(result.texts))
            if result.tables:
                for i, table in enumerate(result.tables, start=1):
                    df = table.export_to_dataframe()
                    parts.append(f"\n\nTable {i}\n" + "-" * (6 + len(str(i))) + "\n")
                    parts.append(self._df_to_markdown(df))  # ASCII pipe table is fine for TXT
        else:
            parts.append(result.document.export_to_markdown())
            if result.document.tables:
                for i, table in enumerate(result.document.tables, start=1):
                    df = table.export_to_dataframe()
                    parts.append(f"\n\nTable {i}\n" + "-" * (6 + len(str(i))) + "\n")
                    parts.append(self._df_to_markdown(df))

        out_file.write_text("\n".join(parts), encoding="utf-8")
        return out_file

    def export_to_csv(self, result):
        """
        Export to CSV.
        - If only text: one CSV with 'Document Text' column.
        - If tables: one CSV per table + optional Text CSV.
        Returns a LIST of file paths.
        """
        output_dir = self._default_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)

        created = []

        if isinstance(result, CombinedResult):
            # Text
            all_text = "\n\n".join(result.texts) if result.texts else ""
            text_csv = output_dir / f"{self.input_file.stem}_Text.csv"
            pd.DataFrame({"Document Text": [all_text]}).to_csv(text_csv, index=False, encoding="utf-8")
            created.append(text_csv)

            # Tables
            for i, table in enumerate(result.tables, start=1):
                df = table.export_to_dataframe()
                table_csv = output_dir / f"{self.input_file.stem}_Table_{i}.csv"
                df.to_csv(table_csv, index=False, encoding="utf-8")
                created.append(table_csv)

            return created

        # Single document
        if result.document.tables:
            # Tables
            for i, table in enumerate(result.document.tables, start=1):
                df = table.export_to_dataframe()
                table_csv = output_dir / f"{self.input_file.stem}_Table_{i}.csv"
                df.to_csv(table_csv, index=False, encoding="utf-8")
                created.append(table_csv)
            # Text
            text = result.document.export_to_markdown()
            text_csv = output_dir / f"{self.input_file.stem}_Text.csv"
            pd.DataFrame({"Document Text": [text]}).to_csv(text_csv, index=False, encoding="utf-8")
            created.append(text_csv)
        else:
            # Only text
            text = result.document.export_to_markdown()
            text_csv = output_dir / f"{self.input_file.stem}_Text.csv"
            pd.DataFrame({"Document Text": [text]}).to_csv(text_csv, index=False, encoding="utf-8")
            created.append(text_csv)

        return created


# ---------------- MAIN ---------------- #

if __name__ == "__main__":
    root = tk.Tk()
    app = DoclingOCRTool(root)
    root.mainloop()