#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GUI tool to convert a Markdown file to PDF and/or DOCX with LaTeX math support.

Requirements:
- pandoc in PATH
- For PDF: a LaTeX engine (xelatex/lualatex/pdflatex) installed and in PATH
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "Markdown → PDF/DOCX (with Math)"
PDF_ENGINES = ["xelatex", "lualatex", "pdflatex"]

def which_or_die(cmd: str):
    if shutil.which(cmd) is None:
        raise FileNotFoundError(f"'{cmd}' not found in PATH")

def run_cmd(cmd):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return proc.stdout.strip()
    except subprocess.CalledProcessError as e:
        err = e.stderr or e.stdout or ""
        raise RuntimeError(f"Command failed ({e.returncode}):\n{' '.join(cmd)}\n\n{err}")

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("680x420")
        self.minsize(640, 400)

        self.input_md = tk.StringVar()
        self.out_dir = tk.StringVar()
        self.reference_docx = tk.StringVar()
        self.resource_path = tk.StringVar()

        # self.make_pdf = tk.BooleanVar(value=True)
        self.make_docx = tk.BooleanVar(value=True)
        self.include_toc = tk.BooleanVar(value=False)
        self.toc_depth = tk.IntVar(value=3)
        # self.pdf_engine = tk.StringVar(value=PDF_ENGINES[0])

        self._build_ui()

    def _build_ui(self):
        pad = {'padx': 10, 'pady': 6}

        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True)

        # Input MD
        row1 = ttk.Frame(frm); row1.pack(fill="x", **pad)
        ttk.Label(row1, text="Markdown file:").pack(side="left")
        ttk.Entry(row1, textvariable=self.input_md).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(row1, text="Browse…", command=self.pick_md).pack(side="left")

        # Output directory
        row2 = ttk.Frame(frm); row2.pack(fill="x", **pad)
        ttk.Label(row2, text="Output folder:").pack(side="left")
        ttk.Entry(row2, textvariable=self.out_dir).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(row2, text="Choose…", command=self.pick_outdir).pack(side="left")

        # Reference DOCX
        row3 = ttk.Frame(frm); row3.pack(fill="x", **pad)
        ttk.Label(row3, text="Reference .docx (optional):").pack(side="left")
        ttk.Entry(row3, textvariable=self.reference_docx).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(row3, text="Browse…", command=self.pick_reference).pack(side="left")

        # Resource path
        row4 = ttk.Frame(frm); row4.pack(fill="x", **pad)
        ttk.Label(row4, text="Resource path(s) (images):").pack(side="left")
        ttk.Entry(row4, textvariable=self.resource_path).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Label(row4, text="(use ':' on Unix, ';' on Windows)").pack(side="left")

        # Options
        row5 = ttk.LabelFrame(frm, text="Options"); row5.pack(fill="x", **pad)
        # ttk.Checkbutton(row5, text="Generate PDF", variable=self.make_pdf).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(row5, text="Generate DOCX", variable=self.make_docx).grid(row=0, column=1, sticky="w", padx=8, pady=6)

        # ttk.Label(row5, text="PDF engine:").grid(row=1, column=0, sticky="w", padx=8)
        # ttk.Combobox(row5, textvariable=self.pdf_engine, values=PDF_ENGINES, state="readonly", width=12)\
        #     .grid(row=1, column=1, sticky="w", padx=8)

        ttk.Checkbutton(row5, text="Include Table of Contents", variable=self.include_toc)\
            .grid(row=2, column=0, sticky="w", padx=8, pady=6)
        ttk.Label(row5, text="TOC depth:").grid(row=2, column=1, sticky="w", padx=8)
        ttk.Spinbox(row5, from_=1, to=6, textvariable=self.toc_depth, width=5).grid(row=2, column=2, sticky="w", padx=8)

        # Actions + Log
        row6 = ttk.Frame(frm); row6.pack(fill="x", **pad)
        ttk.Button(row6, text="Convert", command=self.convert).pack(side="right")
        ttk.Button(row6, text="Quit", command=self.destroy).pack(side="right", padx=8)

        self.log = tk.Text(frm, height=12, wrap="word")
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log_insert("Ready.\n")

    def log_insert(self, msg: str):
        self.log.insert("end", msg)
        self.log.see("end")
        self.update_idletasks()

    def pick_md(self):
        f = filedialog.askopenfilename(
            title="Select Markdown file",
            filetypes=[("Markdown", "*.md *.markdown"), ("All files", "*.*")]
        )
        if f:
            self.input_md.set(f)
            # default out dir to file's folder if empty
            if not self.out_dir.get():
                self.out_dir.set(str(Path(f).parent))

    def pick_outdir(self):
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            self.out_dir.set(d)

    def pick_reference(self):
        f = filedialog.askopenfilename(
            title="Select reference DOCX",
            filetypes=[("Word Document", "*.docx"), ("All files", "*.*")]
        )
        if f:
            self.reference_docx.set(f)

    def convert(self):
        try:
            # Validate selections
            md = Path(self.input_md.get()).expanduser()
            if not md.exists():
                messagebox.showerror(APP_TITLE, "Please select a valid Markdown file.")
                return

            outdir = Path(self.out_dir.get()).expanduser() if self.out_dir.get() else md.parent
            outdir.mkdir(parents=True, exist_ok=True)

            # if not self.make_pdf.get() and not self.make_docx.get():
            #     messagebox.showerror(APP_TITLE, "Select at least one output: PDF and/or DOCX.")
            #     return

            # Check pandoc
            self.log_insert("Checking pandoc...\n")
            which_or_die("pandoc")

            # Base pandoc args with math support
            base = [
                "pandoc",
                str(md),
                "--from", "markdown+tex_math_dollars+tex_math_single_backslash",
            ]

            # Resource paths
            rp = self.resource_path.get().strip()
            if rp:
                base += ["--resource-path", rp]

            # TOC
            if self.include_toc.get():
                base += ["--toc", "--toc-depth", str(self.toc_depth.get())]

            # Metadata example: keep simple; user can extend later if desired
            # base += ["--metadata", "title=" + md.stem]

            stem = md.stem
            outputs = []

            # DOCX
            if self.make_docx.get():
                docx_path = outdir / f"{stem}.docx"
                args = base + ["-o", str(docx_path)]
                ref = self.reference_docx.get().strip()
                if ref:
                    refp = Path(ref).expanduser()
                    if not refp.exists():
                        messagebox.showerror(APP_TITLE, f"Reference DOCX not found:\n{refp}")
                        return
                    args += ["--reference-doc", str(refp)]

                self.log_insert(f"Generating DOCX: {docx_path}\n")
                run_cmd(args)
                outputs.append(docx_path)

            # PDF
            # if self.make_pdf.get():
            #     # Check LaTeX engine
            #     engine = self.pdf_engine.get()
            #     self.log_insert(f"Checking LaTeX engine: {engine}\n")
            #     which_or_die(engine)
            #
            #     pdf_path = outdir / f"{stem}.pdf"
            #     args = base + [
            #         "--pdf-engine", engine,
            #         "-V", "geometry:margin=1in",
            #         "-o", str(pdf_path),
            #     ]
            #     self.log_insert(f"Generating PDF: {pdf_path}\n")
            #     run_cmd(args)
            #     outputs.append(pdf_path)

            self.log_insert("\n✅ Conversion completed.\n")
            for p in outputs:
                self.log_insert(f"• {p}\n")
            messagebox.showinfo(APP_TITLE, "Conversion completed successfully.")

        except FileNotFoundError as e:
            self.log_insert(str(e) + "\n")
            messagebox.showerror(APP_TITLE, str(e))
        except RuntimeError as e:
            self.log_insert(str(e) + "\n")
            messagebox.showerror(APP_TITLE, f"Conversion failed:\n\n{e}")
        except Exception as e:
            self.log_insert(f"Unexpected error: {e}\n")
            messagebox.showerror(APP_TITLE, f"Unexpected error:\n\n{e}")

def main():
    app = App()
    app.mainloop()

if __name__ == "__main__":
    main()
