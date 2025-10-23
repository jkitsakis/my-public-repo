#!/usr/bin/env python3
# merge_docx_gui_pandoc.py
# GUI to select/order .docx files and merge them via Pandoc

import os
import sys
import shutil
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox

APP_TITLE = "Merge DOCX via Pandoc"

def find_pandoc():
    """Return a path to pandoc executable if found, else None."""
    exe = shutil.which("pandoc")
    if exe:
        return exe

    # Windows common install path fallback
    if os.name == "nt":
        candidates = [
            r"C:\Program Files\Pandoc\pandoc.exe",
            r"C:\Program Files (x86)\Pandoc\pandoc.exe",
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
    return None

class MergeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("650x420")
        self.minsize(600, 380)

        # Listbox for selected files
        self.listbox = tk.Listbox(self, selectmode=tk.EXTENDED, activestyle="dotbox")
        self.listbox.grid(row=0, column=0, rowspan=6, sticky="nsew", padx=(10, 5), pady=10)

        # Scrollbar for the listbox
        scrollbar = tk.Scrollbar(self, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, rowspan=6, sticky="ns", pady=10)

        # Right-side controls
        btn_add = tk.Button(self, text="Add .docx…", command=self.add_files)
        btn_remove = tk.Button(self, text="Remove", command=self.remove_selected)
        btn_up = tk.Button(self, text="Move Up", command=lambda: self.move_selected(-1))
        btn_down = tk.Button(self, text="Move Down", command=lambda: self.move_selected(1))
        btn_clear = tk.Button(self, text="Clear All", command=self.clear_all)
        btn_merge = tk.Button(self, text="Merge…", command=self.merge_files)

        btn_add.grid(row=0, column=2, sticky="ew", padx=(5, 10), pady=(10, 5))
        btn_remove.grid(row=1, column=2, sticky="ew", padx=(5, 10), pady=5)
        btn_up.grid(row=2, column=2, sticky="ew", padx=(5, 10), pady=5)
        btn_down.grid(row=3, column=2, sticky="ew", padx=(5, 10), pady=5)
        btn_clear.grid(row=4, column=2, sticky="ew", padx=(5, 10), pady=5)
        btn_merge.grid(row=5, column=2, sticky="ew", padx=(5, 10), pady=(5, 10))

        # Status bar
        self.status = tk.StringVar(value="Ready")
        status_bar = tk.Label(self, textvariable=self.status, anchor="w", relief="sunken")
        status_bar.grid(row=6, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 10))

        # Grid weights
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        # Check Pandoc presence up-front
        self.pandoc_path = find_pandoc()
        if not self.pandoc_path:
            self.status.set("Pandoc not found. Please install Pandoc or add it to PATH.")
            messagebox.showwarning(
                APP_TITLE,
                "Pandoc was not found.\n\nInstall it from https://pandoc.org/installing.html "
                "or add it to your PATH. You can still select files, but merging will fail without Pandoc."
            )

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select .docx files to merge (in any order)",
            filetypes=[("Word documents", "*.docx")],
        )
        if not paths:
            return
        # Append, avoid duplicates
        existing = set(self.listbox.get(0, tk.END))
        for p in paths:
            if p not in existing:
                self.listbox.insert(tk.END, p)
        self.status.set(f"Added {len(paths)} file(s).")

    def remove_selected(self):
        selection = list(self.listbox.curselection())
        if not selection:
            return
        # Remove from bottom up to keep indices valid
        for idx in reversed(selection):
            self.listbox.delete(idx)
        self.status.set("Removed selected file(s).")

    def move_selected(self, direction: int):
        """Move selected items up (-1) or down (+1)."""
        selection = list(self.listbox.curselection())
        if not selection:
            return

        items = list(self.listbox.get(0, tk.END))
        # Calculate new positions
        new_selection = []
        if direction < 0:
            # Move up: iterate in order
            for idx in selection:
                if idx == 0:
                    new_selection.append(idx)
                    continue
                items[idx - 1], items[idx] = items[idx], items[idx - 1]
                new_selection.append(idx - 1)
        else:
            # Move down: iterate reversed
            for idx in reversed(selection):
                if idx == len(items) - 1:
                    new_selection.append(idx)
                    continue
                items[idx + 1], items[idx] = items[idx], items[idx + 1]
                new_selection.append(idx + 1)
            new_selection.reverse()

        # Refresh listbox content and selection
        self.listbox.delete(0, tk.END)
        for it in items:
            self.listbox.insert(tk.END, it)
        self.listbox.selection_clear(0, tk.END)
        for idx in new_selection:
            self.listbox.selection_set(idx)
        self.status.set("Reordered file(s).")

    def clear_all(self):
        self.listbox.delete(0, tk.END)
        self.status.set("Cleared.")

    def merge_files(self):
        # Validate pandoc
        if not self.pandoc_path:
            self.pandoc_path = find_pandoc()
            if not self.pandoc_path:
                messagebox.showerror(APP_TITLE, "Pandoc not found. Install Pandoc or add it to PATH.")
                return

        files = list(self.listbox.get(0, tk.END))
        if len(files) < 2:
            messagebox.showinfo(APP_TITLE, "Please add at least two .docx files.")
            return

        # Ensure all exist and are .docx
        for p in files:
            if not os.path.isfile(p):
                messagebox.showerror(APP_TITLE, f"File not found:\n{p}")
                return
            if not p.lower().endswith(".docx"):
                messagebox.showerror(APP_TITLE, f"Not a .docx file:\n{p}")
                return

        out_path = filedialog.asksaveasfilename(
            defaultextension=".docx",
            filetypes=[("Word document", "*.docx")],
            title="Save merged document as…",
            initialfile="merged.docx",
        )
        if not out_path:
            return

        # Build pandoc command
        cmd = [self.pandoc_path, *files, "-o", out_path]

        # Run
        self.status.set("Merging…")
        self.update_idletasks()
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                shell=False,
            )
            if proc.returncode != 0:
                message = f"Pandoc failed with exit code {proc.returncode}.\n\nSTDERR:\n{proc.stderr}"
                messagebox.showerror(APP_TITLE, message)
                self.status.set("Merge failed.")
                return
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Error running Pandoc:\n{e}")
            self.status.set("Merge failed.")
            return

        self.status.set(f"Done → {out_path}")
        messagebox.showinfo(APP_TITLE, f"✅ Merged {len(files)} files:\n\n{out_path}")

def main():
    app = MergeApp()
    app.mainloop()

if __name__ == "__main__":
    main()
