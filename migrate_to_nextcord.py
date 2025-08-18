#!/usr/bin/env python3
import argparse, re, sys
from pathlib import Path

SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".venv", "venv", ".tox", ".pytest_cache"}

# Regex ciblant UNIQUEMENT les lignes d'import (évite de toucher les strings/usage du code)
PATTERNS = [
    # import discord
    (re.compile(r"^(\s*)import\s+discord(\s*(?:as\s+\w+)?\s*)$", re.MULTILINE),
     r"\1import nextcord as discord\2"),
    # from discord import ...
    (re.compile(r"^(\s*)from\s+discord\s+import\s+", re.MULTILINE),
     r"\1from nextcord import "),
    # from discord.ext import ...
    (re.compile(r"^(\s*)from\s+discord\.ext\s+import\s+", re.MULTILINE),
     r"\1from nextcord.ext import "),
    # from discord.ui import ...
    (re.compile(r"^(\s*)from\s+discord\.ui\s+import\s+", re.MULTILINE),
     r"\1from nextcord.ui import "),
    # from discord.app_commands import ...
    (re.compile(r"^(\s*)from\s+discord\.app_commands\s+import\s+", re.MULTILINE),
     r"\1from nextcord.app_commands import "),
    # from discord.something import ...
    (re.compile(r"^(\s*)from\s+discord(\.[\w\.]+)?\s+import\s+", re.MULTILINE),
     r"\1from nextcord\2 import "),
]

def rewrite_text(text: str) -> str:
    new = text
    for rx, repl in PATTERNS:
        new = rx.sub(repl, new)
    return new

def should_skip(path: Path) -> bool:
    parts = set(p.name for p in path.parents)
    return any(d in parts for d in SKIP_DIRS)

def main():
    ap = argparse.ArgumentParser(description="Migrate discord.py imports to nextcord imports.")
    ap.add_argument("root", nargs="?", default=".", help="Repo root (default: current directory)")
    ap.add_argument("--write", action="store_true", help="Actually write changes (default: dry-run)")
    ap.add_argument("--ext", nargs="*", default=[".py", ".pyi"], help="File extensions to process")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    changed_files = []

    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if should_skip(path):
            continue
        if path.suffix.lower() not in args.ext:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        new = rewrite_text(text)
        if new != text:
            changed_files.append(path)
            if args.write:
                path.write_text(new, encoding="utf-8")

    if changed_files:
        if args.write:
            print(f"✅ Modifiés ({len(changed_files)} fichiers) :")
        else:
            print(f"🔎 Dry-run — à modifier ({len(changed_files)} fichiers) :")
        for p in changed_files:
            print(" -", p.relative_to(root))
        if not args.write:
            print("\nAstuce: relance avec --write pour appliquer.")
    else:
        print("Aucun import discord.py détecté à migrer.")

if __name__ == "__main__":
    sys.exit(main())
