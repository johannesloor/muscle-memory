"""INDEX.md parsing and editing: CRLF-tolerant, preserving whatever newline style is on disk.

INDEX.md is a Markdown file with one '## Area' heading per area and one line per script
underneath it. Callers read it with encoding="utf-8", newline="" so the exact bytes (including
CRLF, if a human hand-edited it on Windows) are preserved until written back.
"""
import re


def parse_index(text):
    """Return [{'area', 'line', 'script'}] for every entry line under a '##' heading.

    CRLF-tolerant: str.splitlines() treats '\\r\\n', '\\r' and '\\n' alike, so a file with either
    line ending parses the same way.
    """
    entries, area = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            area = line[3:].strip()
        elif area and line and not line.startswith("#"):
            m = re.match(r"([\w.\-/]+\.\w+)", line)
            entries.append({"area": area, "line": line, "script": m.group(1) if m else ""})
    return entries


def add_index_line(text, area, entry):
    """Insert entry as the last line of the '## area' section, creating the section if needed.

    Keeps the file's existing newline style: if text uses '\\r\\n' anywhere, the result uses
    '\\r\\n' throughout; otherwise plain '\\n'.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").rstrip("\n").split("\n")
    start = next((i for i, l in enumerate(lines) if l.strip() == f"## {area}"), None)
    if start is None:
        result = "\n".join(lines) + f"\n\n## {area}\n\n{entry}\n"
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        last = max([i for i in range(start + 1, end) if lines[i].strip()] or [start])
        if last == start:
            lines[start + 1:start + 1] = ["", entry]
        else:
            lines.insert(last + 1, entry)
        result = "\n".join(lines) + "\n"
    return result.replace("\n", newline) if newline != "\n" else result
