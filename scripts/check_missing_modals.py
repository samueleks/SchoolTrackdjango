"""Detecta modales referenciados en JS pero ausentes en HTML."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "login" / "Templates" / "administrador"

for path in sorted(ROOT.glob("*.html")):
    text = path.read_text(encoding="utf-8")
    js_ids = set(re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", text))
    html_ids = set(re.findall(r'id=["\']([^"\']+)["\']', text))
    missing = sorted(js_ids - html_ids)
    if missing:
        print(f"{path.name}: missing {missing}")
