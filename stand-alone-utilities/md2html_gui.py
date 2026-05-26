#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Markdown → HTML GUI converter.

This tool follows the same Tkinter GUI style as md2doc_gui.py,
but generates standalone HTML using the CSS/visual structure style of html_export.py.

Key features:
- Sticky left Table of Contents sidebar
- Real clickable heading anchors
- LaTeX math support via MathJax:
  - $$ ... $$
  - $ ... $
  - \( ... \)
  - \[ ... \]
- Stable Markdown table rendering
- Fixes collapsed one-line Markdown tables often produced by LLMs
- Can optionally load custom CSS
- Does NOT require pandoc
"""

from __future__ import annotations

import os
import re
import sys
import subprocess
import traceback
from datetime import datetime
from html import escape
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "Markdown → HTML"
DEFAULT_SUBTITLE = "Standalone HTML export generated from Markdown."


# ---------------------------------------------------------------------
# CSS based on html_export.py, extended with sticky left TOC sidebar.
# ---------------------------------------------------------------------

DEFAULT_REPORT_CSS = """
:root {
    --bg: #f4f7fb;
    --paper: #ffffff;
    --text: #172033;
    --muted: #667085;
    --border: #d9e2ec;
    --primary: #1d4ed8;
    --primary-soft: #e8f0ff;
    --success-bg: #dcfce7;
    --success-text: #166534;
    --warning-bg: #fef3c7;
    --warning-text: #92400e;
    --danger-bg: #fee2e2;
    --danger-text: #991b1b;
    --code-bg: #0f172a;
    --code-text: #e5e7eb;
}

* {
    box-sizing: border-box;
}

html {
    scroll-behavior: smooth;
}

body {
    margin: 0;
    padding: 32px;
    background: var(--bg);
    color: var(--text);
    font-family: "Inter", "Segoe UI", Roboto, Arial, sans-serif;
    line-height: 1.65;
}

.report-shell {
    max-width: 1440px;
    margin: 0 auto;
}

.report-header {
    background: linear-gradient(135deg, #0f3b8f, #1d4ed8);
    color: white;
    border-radius: 22px;
    padding: 34px 38px;
    margin-bottom: 24px;
    box-shadow: 0 18px 45px rgba(15, 59, 143, 0.22);
}

.report-header h1 {
    margin: 0 0 10px 0;
    color: white;
    font-size: 30px;
    line-height: 1.2;
}

.report-header p {
    margin: 0;
    color: #dbeafe;
    font-size: 15px;
}

.report-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 18px;
}

.meta-pill {
    background: rgba(255, 255, 255, 0.16);
    border: 1px solid rgba(255, 255, 255, 0.22);
    color: white;
    padding: 7px 12px;
    border-radius: 999px;
    font-size: 13px;
}

.report-layout {
    display: grid;
    grid-template-columns: 300px minmax(0, 1fr);
    gap: 24px;
    align-items: start;
}

.toc-sidebar {
    position: sticky;
    top: 24px;
    max-height: calc(100vh - 48px);
    overflow-y: auto;
    background: var(--paper);
    border: 1px solid var(--border);
    border-radius: 18px;
    padding: 18px;
    box-shadow: 0 14px 40px rgba(15, 23, 42, 0.08);
}

.toc-sidebar h2 {
    margin: 0 0 12px 0;
    padding: 0;
    border: none;
    color: #102a56;
    font-size: 16px;
}

.toc-sidebar ol {
    list-style: none;
    margin: 0;
    padding: 0;
}

.toc-sidebar li {
    margin: 3px 0;
}

.toc-sidebar a {
    display: block;
    padding: 7px 9px;
    border-radius: 10px;
    color: #334155;
    text-decoration: none;
    font-size: 13px;
    line-height: 1.35;
}

.toc-sidebar a:hover {
    background: var(--primary-soft);
    color: var(--primary);
    text-decoration: none;
}

.toc-l2 a {
    padding-left: 18px;
}

.toc-l3 a {
    padding-left: 30px;
    font-size: 12px;
}

.report-content {
    min-width: 0;
    background: var(--paper);
    border: 1px solid var(--border);
    border-radius: 22px;
    padding: 34px 38px;
    box-shadow: 0 14px 40px rgba(15, 23, 42, 0.08);
}

h1, h2, h3, h4 {
    color: #102a56;
    line-height: 1.3;
    scroll-margin-top: 24px;
}

.report-content h1 {
    font-size: 28px;
    padding-bottom: 12px;
    border-bottom: 2px solid var(--primary-soft);
}

.report-content h2 {
    margin-top: 34px;
    font-size: 23px;
    padding-left: 12px;
    border-left: 5px solid var(--primary);
}

.report-content h3 {
    margin-top: 26px;
    font-size: 19px;
}

.report-content h4 {
    margin-top: 22px;
    font-size: 16px;
}

p {
    margin: 12px 0;
}

ul, ol {
    padding-left: 26px;
}

li {
    margin: 6px 0;
}

strong {
    color: #0f3b8f;
}

a {
    color: var(--primary);
}

.table-wrap {
    width: 100%;
    overflow-x: auto;
    margin: 18px 0 28px 0;
    border: 1px solid var(--border);
    border-radius: 16px;
    background: white;
}

table {
    width: 100%;
    min-width: 980px;
    border-collapse: collapse;
    font-size: 14px;
}

thead {
    background: #eff6ff;
}

th {
    color: #102a56;
    font-weight: 700;
    text-align: left;
    white-space: nowrap;
}

th, td {
    border-bottom: 1px solid var(--border);
    border-right: 1px solid var(--border);
    padding: 11px 13px;
    vertical-align: top;
}

th:last-child,
td:last-child {
    border-right: none;
}

tbody tr:nth-child(even) {
    background: #f8fafc;
}

tbody tr:hover {
    background: #eef6ff;
}

td code,
p code,
li code {
    background: #eef2ff;
    color: #1e3a8a;
    padding: 2px 6px;
    border-radius: 6px;
    font-family: "JetBrains Mono", Consolas, monospace;
    font-size: 0.92em;
}

pre {
    background: var(--code-bg);
    color: var(--code-text);
    padding: 18px;
    border-radius: 16px;
    overflow-x: auto;
    border: 1px solid #1e293b;
}

pre code {
    background: transparent;
    color: inherit;
    padding: 0;
}

blockquote {
    margin: 18px 0;
    padding: 14px 18px;
    border-left: 5px solid var(--primary);
    background: #f8fbff;
    border-radius: 12px;
    color: #334155;
}

hr {
    border: none;
    height: 1px;
    background: var(--border);
    margin: 28px 0;
}

.badge {
    display: inline-block;
    padding: 3px 8px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 700;
}

.badge-high {
    background: var(--success-bg);
    color: var(--success-text);
}

.badge-medium {
    background: var(--warning-bg);
    color: var(--warning-text);
}

.badge-low {
    background: var(--danger-bg);
    color: var(--danger-text);
}

mjx-container {
    overflow-x: auto;
    overflow-y: hidden;
    max-width: 100%;
    padding: 2px 0;
}

.footer {
    margin-top: 22px;
    color: var(--muted);
    text-align: center;
    font-size: 13px;
}

@media (max-width: 980px) {
    .report-layout {
        grid-template-columns: 1fr;
    }

    .toc-sidebar {
        position: static;
        max-height: none;
    }
}

@media print {
    body {
        background: white;
        padding: 0;
    }

    .report-header,
    .report-content,
    .toc-sidebar {
        box-shadow: none;
    }

    .report-shell {
        max-width: none;
    }

    .report-layout {
        grid-template-columns: 1fr;
    }

    .toc-sidebar {
        position: static;
        max-height: none;
        page-break-after: always;
    }

    .table-wrap {
        overflow: visible;
    }

    table {
        min-width: 0;
        font-size: 11px;
    }
}
"""


# ---------------------------------------------------------------------
# Optional: reuse CSS from html_export.py if available.
# We intentionally keep our own renderer because it supports:
# - real heading IDs for sidebar links
# - MathJax-safe LaTeX preservation
# ---------------------------------------------------------------------

def _try_load_html_export_css() -> str | None:
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))

    try:
        import html_export  # type: ignore

        css = getattr(html_export, "DEFAULT_REPORT_CSS", None)
        return css if isinstance(css, str) else None
    except Exception:
        return None


def merge_with_sidebar_css(base_css: str) -> str:
    """
    If user/html_export CSS is loaded, append the required sidebar/MathJax layout CSS.
    """
    required = """
html {
    scroll-behavior: smooth;
}

.report-shell {
    max-width: 1440px;
}

.report-layout {
    display: grid;
    grid-template-columns: 300px minmax(0, 1fr);
    gap: 24px;
    align-items: start;
}

.toc-sidebar {
    position: sticky;
    top: 24px;
    max-height: calc(100vh - 48px);
    overflow-y: auto;
    background: var(--paper, #ffffff);
    border: 1px solid var(--border, #d9e2ec);
    border-radius: 18px;
    padding: 18px;
    box-shadow: 0 14px 40px rgba(15, 23, 42, 0.08);
}

.toc-sidebar h2 {
    margin: 0 0 12px 0;
    padding: 0;
    border: none;
    color: #102a56;
    font-size: 16px;
}

.toc-sidebar ol {
    list-style: none;
    margin: 0;
    padding: 0;
}

.toc-sidebar li {
    margin: 3px 0;
}

.toc-sidebar a {
    display: block;
    padding: 7px 9px;
    border-radius: 10px;
    color: #334155;
    text-decoration: none;
    font-size: 13px;
    line-height: 1.35;
}

.toc-sidebar a:hover {
    background: var(--primary-soft, #e8f0ff);
    color: var(--primary, #1d4ed8);
    text-decoration: none;
}

.toc-l2 a {
    padding-left: 18px;
}

.toc-l3 a {
    padding-left: 30px;
    font-size: 12px;
}

.report-layout .report-content {
    min-width: 0;
}

h1, h2, h3, h4 {
    scroll-margin-top: 24px;
}

mjx-container {
    overflow-x: auto;
    overflow-y: hidden;
    max-width: 100%;
    padding: 2px 0;
}

@media (max-width: 980px) {
    .report-layout {
        grid-template-columns: 1fr;
    }

    .toc-sidebar {
        position: static;
        max-height: none;
    }
}
"""

    if ".toc-sidebar" in base_css and ".report-layout" in base_css:
        return base_css

    return base_css + "\n" + required


# ---------------------------------------------------------------------
# Markdown normalization and rendering.
# ---------------------------------------------------------------------

def read_text_safe(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp1253", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue

    return path.read_text(errors="replace")


def html_download_filename(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", name.strip())
    safe = safe.strip("_") or "report"

    if not safe.lower().endswith(".html"):
        safe += ".html"

    return safe


def normalize_markdown_for_html(markdown_text: str) -> str:
    """
    Fix common LLM/RAG markdown issues before rendering.

    Important fix:
    Collapsed markdown table rows like:
        | A | B | |---|---| | 1 | 2 |
    become:
        | A | B |
        |---|---|
        | 1 | 2 |
    """
    text = markdown_text or ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    parts = re.split(r"(```.*?```)", text, flags=re.DOTALL)
    normalized_parts: list[str] = []

    for part in parts:
        if part.startswith("```") and part.endswith("```"):
            normalized_parts.append(part)
        else:
            normalized_parts.append(_normalize_collapsed_tables_in_text(part))

    return "".join(normalized_parts)


def _normalize_collapsed_tables_in_text(text: str) -> str:
    lines = text.split("\n")
    output: list[str] = []

    for line in lines:
        stripped = line.strip()

        if _looks_like_collapsed_table(stripped):
            output.extend(_split_collapsed_table_line(stripped))
        else:
            output.append(line)

    return "\n".join(output)


def _looks_like_collapsed_table(line: str) -> bool:
    if not line.startswith("|"):
        return False

    has_separator = re.search(r"\|\s*:?-{3,}:?\s*\|", line) is not None
    has_multiple_rows = line.count("| |") >= 1 or re.search(r"\|\s+\|", line) is not None

    return has_separator and has_multiple_rows


def _split_collapsed_table_line(line: str) -> list[str]:
    text = re.sub(r"\|\s+\|", "|\n|", line.strip())
    rows = [row.strip() for row in text.split("\n") if row.strip()]

    fixed_rows: list[str] = []

    for row in rows:
        if not row.startswith("|"):
            row = "| " + row
        if not row.endswith("|"):
            row += " |"
        fixed_rows.append(row)

    return fixed_rows


def protect_math(text: str) -> tuple[str, list[str]]:
    """
    Temporarily remove math from text so escaping/markdown conversion
    does not corrupt LaTeX.
    """
    placeholders: list[str] = []

    pattern = re.compile(
        r"(\$\$.*?\$\$|\\\[.*?\\\]|\\\(.*?\\\)|(?<!\\)\$(?!\$).*?(?<!\\)\$)",
        flags=re.DOTALL,
    )

    def stash(match: re.Match) -> str:
        placeholders.append(match.group(0))
        return f"@@MD3HTML_MATH_{len(placeholders) - 1}@@"

    return pattern.sub(stash, text), placeholders


def restore_math(text: str, placeholders: list[str]) -> str:
    for index, value in enumerate(placeholders):
        text = text.replace(f"@@MD3HTML_MATH_{index}@@", value)

    return text


def render_inline(value: str) -> str:
    value, math_parts = protect_math(value)

    value = escape(value)

    value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
    value = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", value)

    value = _auto_code_java_symbols(value)
    value = _render_confidence_badges(value)

    return restore_math(value, math_parts)


def _auto_code_java_symbols(value: str) -> str:
    patterns = [
        r"\b[A-Z][A-Za-z0-9_]*\.[a-zA-Z_][A-Za-z0-9_]*\(\)",
        r"\b[a-zA-Z_][A-Za-z0-9_]*\.[a-zA-Z_][A-Za-z0-9_]*\(\)",
        r"\b[A-Z][A-Za-z0-9_]*\([^\)]*\)",
        r"\b[A-Z][A-Za-z0-9_]*\.[A-Z_][A-Z0-9_]*\b",
    ]

    for pattern in patterns:
        value = re.sub(pattern, lambda m: f"<code>{m.group(0)}</code>", value)

    return value


def _render_confidence_badges(value: str) -> str:
    value = re.sub(r"\bHigh\b", '<span class="badge badge-high">High</span>', value)
    value = re.sub(r"\bMedium\b", '<span class="badge badge-medium">Medium</span>', value)
    value = re.sub(r"\bLow\b", '<span class="badge badge-low">Low</span>', value)
    return value


def slugify(value: str, existing: set[str]) -> str:
    raw = value.strip().lower()
    raw = re.sub(r"`([^`]+)`", r"\1", raw)
    raw = re.sub(r"\*\*([^*]+)\*\*", r"\1", raw)
    raw = re.sub(r"[^a-zA-Z0-9\u0370-\u03FF]+", "-", raw)
    raw = raw.strip("-") or "section"

    slug = raw
    counter = 2

    while slug in existing:
        slug = f"{raw}-{counter}"
        counter += 1

    existing.add(slug)
    return slug


def _is_markdown_table_start(lines: list[str], index: int) -> bool:
    current = lines[index].strip()

    if not current.startswith("|") or not current.endswith("|"):
        return False

    if index + 1 >= len(lines):
        return False

    next_line = lines[index + 1].strip()

    if not next_line.startswith("|") or not next_line.endswith("|"):
        return False

    cells = [cell.strip() for cell in next_line.strip("|").split("|")]
    return all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


def _normalize_table_row(row: list[str], column_count: int) -> list[str]:
    if len(row) == column_count:
        return row

    if len(row) < column_count:
        return row + [""] * (column_count - len(row))

    fixed = row[: column_count - 1]
    fixed.append(" | ".join(row[column_count - 1 :]))
    return fixed


def _render_markdown_table(table_lines: list[str]) -> str:
    rows: list[list[str]] = []

    for line in table_lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows.append(cells)

    if len(rows) < 2:
        return f"<p>{render_inline(table_lines[0])}</p>"

    header = rows[0]
    body = rows[2:]
    column_count = len(header)
    normalized_body = [_normalize_table_row(row, column_count) for row in body]

    html: list[str] = []
    html.append('<div class="table-wrap">')
    html.append("<table>")
    html.append("<thead><tr>")

    for cell in header:
        html.append(f"<th>{render_inline(cell)}</th>")

    html.append("</tr></thead>")
    html.append("<tbody>")

    for row in normalized_body:
        html.append("<tr>")
        for cell in row:
            html.append(f"<td>{render_inline(cell)}</td>")
        html.append("</tr>")

    html.append("</tbody>")
    html.append("</table>")
    html.append("</div>")

    return "\n".join(html)


def markdown_to_html(markdown_text: str, include_toc: bool) -> tuple[str, str]:
    """
    Returns:
    - sidebar HTML
    - body HTML
    """
    text = normalize_markdown_for_html(markdown_text)
    lines = text.splitlines()

    html_parts: list[str] = []
    headings: list[tuple[int, str, str]] = []
    used_slugs: set[str] = set()

    in_code = False
    code_lines: list[str] = []
    code_lang = ""
    in_ul = False
    in_ol = False
    paragraph_lines: list[str] = []

    def close_lists() -> None:
        nonlocal in_ul, in_ol

        if in_ul:
            html_parts.append("</ul>")
            in_ul = False

        if in_ol:
            html_parts.append("</ol>")
            in_ol = False

    def flush_paragraph() -> None:
        nonlocal paragraph_lines

        if not paragraph_lines:
            return

        text_value = " ".join(line.strip() for line in paragraph_lines).strip()

        if text_value:
            html_parts.append(f"<p>{render_inline(text_value)}</p>")

        paragraph_lines = []

    def flush_code() -> None:
        nonlocal code_lines, code_lang

        code = "\n".join(code_lines)
        lang_class = f' class="language-{escape(code_lang)}"' if code_lang else ""
        html_parts.append(f"<pre><code{lang_class}>{escape(code)}</code></pre>")
        code_lines = []
        code_lang = ""

    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            if not in_code:
                flush_paragraph()
                close_lists()
                in_code = True
                code_lang = stripped[3:].strip()
                code_lines = []
            else:
                in_code = False
                flush_code()

            i += 1
            continue

        if in_code:
            code_lines.append(line)
            i += 1
            continue

        if not stripped:
            flush_paragraph()
            close_lists()
            i += 1
            continue

        if _is_markdown_table_start(lines, i):
            flush_paragraph()
            close_lists()

            table_lines: list[str] = []

            while i < len(lines):
                candidate = lines[i].strip()
                if candidate.startswith("|") and candidate.endswith("|"):
                    table_lines.append(candidate)
                    i += 1
                else:
                    break

            html_parts.append(_render_markdown_table(table_lines))
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)

        if heading_match:
            flush_paragraph()
            close_lists()

            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            anchor = slugify(heading_text, used_slugs)
            headings.append((level, anchor, re.sub(r"[`*_]", "", heading_text)))
            html_parts.append(f'<h{level} id="{escape(anchor)}">{render_inline(heading_text)}</h{level}>')
            i += 1
            continue

        if stripped == "---":
            flush_paragraph()
            close_lists()
            html_parts.append("<hr />")
            i += 1
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            close_lists()
            html_parts.append(f"<blockquote>{render_inline(stripped[1:].strip())}</blockquote>")
            i += 1
            continue

        unordered = re.match(r"^\s*[-*]\s+(.*)$", line)
        if unordered:
            flush_paragraph()
            if in_ol:
                html_parts.append("</ol>")
                in_ol = False
            if not in_ul:
                html_parts.append("<ul>")
                in_ul = True
            html_parts.append(f"<li>{render_inline(unordered.group(1).strip())}</li>")
            i += 1
            continue

        ordered = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if ordered:
            flush_paragraph()
            if in_ul:
                html_parts.append("</ul>")
                in_ul = False
            if not in_ol:
                html_parts.append("<ol>")
                in_ol = True
            html_parts.append(f"<li>{render_inline(ordered.group(1).strip())}</li>")
            i += 1
            continue

        paragraph_lines.append(line)
        i += 1

    flush_paragraph()
    close_lists()

    if in_code:
        flush_code()

    body_html = "\n".join(html_parts)
    toc_html = render_toc(headings) if include_toc else ""

    return toc_html, body_html


def render_toc(headings: list[tuple[int, str, str]]) -> str:
    visible = [(level, anchor, title) for level, anchor, title in headings if level <= 3]

    if not visible:
        return ""

    toc: list[str] = []
    toc.append('<nav class="toc-sidebar" aria-label="Table of Contents">')
    toc.append("<h2>Table of Contents</h2>")
    toc.append("<ol>")

    for level, anchor, title in visible:
        toc.append(
            f'<li class="toc-l{level}"><a href="#{escape(anchor)}">{escape(title)}</a></li>'
        )

    toc.append("</ol>")
    toc.append("</nav>")

    return "\n".join(toc)


def render_html_report(
    *,
    title: str,
    subtitle: str,
    body_markdown: str,
    project_name: str,
    report_type: str,
    css: str,
    include_toc: bool,
) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    toc_html, body_html = markdown_to_html(body_markdown, include_toc=include_toc)

    if not toc_html:
        toc_html = '<nav class="toc-sidebar" aria-label="Table of Contents"><h2>Table of Contents</h2><p>No headings found.</p></nav>'

    return f"""<!DOCTYPE html>
<html lang="el">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape(title)}</title>

    <script>
        window.MathJax = {{
            tex: {{
                inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
                displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],
                processEscapes: true
            }},
            svg: {{
                fontCache: 'global'
            }}
        }};
    </script>
    <script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>

    <style>
{css}
    </style>
</head>
<body>
    <main class="report-shell">
        <header class="report-header">
            <h1>{escape(title)}</h1>
            <p>{escape(subtitle)}</p>
            <div class="report-meta">
                <span class="meta-pill">Project: {escape(project_name or "N/A")}</span>
                <span class="meta-pill">Report: {escape(report_type or "Markdown HTML Export")}</span>
                <span class="meta-pill">Generated: {escape(generated_at)}</span>
            </div>
        </header>

        <div class="report-layout">
            {toc_html}

            <section class="report-content">
                {body_html}
            </section>
        </div>

        <footer class="footer">
            Generated by md3html
        </footer>
    </main>
</body>
</html>
"""


# ---------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("760x560")
        self.minsize(720, 520)

        self.input_md = tk.StringVar()
        self.out_dir = tk.StringVar()
        self.custom_css = tk.StringVar()

        self.title_text = tk.StringVar()
        self.subtitle_text = tk.StringVar(value=DEFAULT_SUBTITLE)
        self.project_name = tk.StringVar(value="N/A")
        self.report_type = tk.StringVar(value="Markdown HTML Export")

        self.include_toc = tk.BooleanVar(value=True)
        self.open_after_convert = tk.BooleanVar(value=False)
        self.prefer_html_export_css = tk.BooleanVar(value=True)

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True)

        row1 = ttk.Frame(frm)
        row1.pack(fill="x", **pad)
        ttk.Label(row1, text="Markdown file:").pack(side="left")
        ttk.Entry(row1, textvariable=self.input_md).pack(
            side="left", fill="x", expand=True, padx=8
        )
        ttk.Button(row1, text="Browse…", command=self.pick_md).pack(side="left")

        row2 = ttk.Frame(frm)
        row2.pack(fill="x", **pad)
        ttk.Label(row2, text="Output folder:").pack(side="left")
        ttk.Entry(row2, textvariable=self.out_dir).pack(
            side="left", fill="x", expand=True, padx=8
        )
        ttk.Button(row2, text="Choose…", command=self.pick_outdir).pack(side="left")

        row3 = ttk.Frame(frm)
        row3.pack(fill="x", **pad)
        ttk.Label(row3, text="Custom CSS file (optional):").pack(side="left")
        ttk.Entry(row3, textvariable=self.custom_css).pack(
            side="left", fill="x", expand=True, padx=8
        )
        ttk.Button(row3, text="Browse…", command=self.pick_css).pack(side="left")

        meta = ttk.LabelFrame(frm, text="HTML Metadata")
        meta.pack(fill="x", **pad)

        ttk.Label(meta, text="Title:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(meta, textvariable=self.title_text).grid(
            row=0, column=1, sticky="ew", padx=8, pady=6
        )

        ttk.Label(meta, text="Subtitle:").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(meta, textvariable=self.subtitle_text).grid(
            row=1, column=1, sticky="ew", padx=8, pady=6
        )

        ttk.Label(meta, text="Project:").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(meta, textvariable=self.project_name).grid(
            row=2, column=1, sticky="ew", padx=8, pady=6
        )

        ttk.Label(meta, text="Report type:").grid(row=3, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(meta, textvariable=self.report_type).grid(
            row=3, column=1, sticky="ew", padx=8, pady=6
        )

        meta.columnconfigure(1, weight=1)

        opts = ttk.LabelFrame(frm, text="Options")
        opts.pack(fill="x", **pad)

        ttk.Checkbutton(
            opts,
            text="Include left Table of Contents",
            variable=self.include_toc,
        ).grid(row=0, column=0, sticky="w", padx=8, pady=6)

        ttk.Checkbutton(
            opts,
            text="Use html_export.py CSS when available",
            variable=self.prefer_html_export_css,
        ).grid(row=0, column=1, sticky="w", padx=8, pady=6)

        ttk.Checkbutton(
            opts,
            text="Open HTML after conversion",
            variable=self.open_after_convert,
        ).grid(row=1, column=0, sticky="w", padx=8, pady=6)

        row_actions = ttk.Frame(frm)
        row_actions.pack(fill="x", **pad)

        ttk.Button(row_actions, text="Convert", command=self.convert).pack(side="right")
        ttk.Button(row_actions, text="Quit", command=self.destroy).pack(side="right", padx=8)

        self.log = tk.Text(frm, height=13, wrap="word")
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log_insert("Ready.\n")

    def log_insert(self, msg: str):
        self.log.insert("end", msg)
        self.log.see("end")
        self.update_idletasks()

    def pick_md(self):
        file_path = filedialog.askopenfilename(
            title="Select Markdown file",
            filetypes=[("Markdown", "*.md *.markdown"), ("All files", "*.*")],
        )

        if not file_path:
            return

        path = Path(file_path)
        self.input_md.set(str(path))

        if not self.out_dir.get():
            self.out_dir.set(str(path.parent))

        if not self.title_text.get().strip():
            self.title_text.set(path.stem)

    def pick_outdir(self):
        directory = filedialog.askdirectory(title="Select output folder")
        if directory:
            self.out_dir.set(directory)

    def pick_css(self):
        file_path = filedialog.askopenfilename(
            title="Select CSS file",
            filetypes=[("CSS", "*.css"), ("All files", "*.*")],
        )

        if file_path:
            self.custom_css.set(file_path)

    def _load_css(self) -> str:
        css_path = self.custom_css.get().strip()

        if css_path:
            path = Path(css_path).expanduser()

            if not path.exists():
                raise FileNotFoundError(f"Custom CSS file not found:\n{path}")

            return merge_with_sidebar_css(read_text_safe(path))

        if self.prefer_html_export_css.get():
            css = _try_load_html_export_css()
            if css:
                self.log_insert("Using CSS from html_export.py + sidebar extensions.\n")
                return merge_with_sidebar_css(css)

        self.log_insert("Using embedded CSS.\n")
        return DEFAULT_REPORT_CSS

    def convert(self):
        try:
            md_path = Path(self.input_md.get()).expanduser()

            if not md_path.exists():
                messagebox.showerror(APP_TITLE, "Please select a valid Markdown file.")
                return

            outdir = (
                Path(self.out_dir.get()).expanduser()
                if self.out_dir.get().strip()
                else md_path.parent
            )
            outdir.mkdir(parents=True, exist_ok=True)

            self.log_insert(f"Reading Markdown: {md_path}\n")
            markdown_text = read_text_safe(md_path)

            self.log_insert("Loading CSS...\n")
            css = self._load_css()

            title = self.title_text.get().strip() or md_path.stem
            subtitle = self.subtitle_text.get().strip() or DEFAULT_SUBTITLE
            project_name = self.project_name.get().strip() or "N/A"
            report_type = self.report_type.get().strip() or "Markdown HTML Export"

            self.log_insert("Rendering HTML with left sticky TOC and MathJax...\n")
            html_text = render_html_report(
                title=title,
                subtitle=subtitle,
                body_markdown=markdown_text,
                project_name=project_name,
                report_type=report_type,
                css=css,
                include_toc=self.include_toc.get(),
            )

            output_name = html_download_filename(md_path.stem)
            output_path = outdir / output_name

            self.log_insert(f"Writing HTML: {output_path}\n")
            output_path.write_text(html_text, encoding="utf-8")

            self.log_insert("\n✅ Conversion completed.\n")
            self.log_insert(f"• {output_path}\n")

            if self.open_after_convert.get():
                self._open_file(output_path)

            messagebox.showinfo(APP_TITLE, f"HTML created successfully:\n\n{output_path}")

        except Exception as exc:
            error_text = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.log_insert(f"\n❌ Conversion failed:\n{error_text}\n")
            messagebox.showerror(APP_TITLE, f"Conversion failed:\n\n{error_text}")

    def _open_file(self, path: Path):
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            self.log_insert(f"Could not open file automatically: {exc}\n")


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
