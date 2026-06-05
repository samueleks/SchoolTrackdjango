"""Detecta modales referenciados en JS pero ausentes en HTML."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "login" / "Templates"
ROLES = sys.argv[1:] if len(sys.argv) > 1 else [
    "administrador", "administrativo", "maestro", "alumno"
]

missing_any = False
for role in ROLES:
    folder = ROOT / role
    if not folder.exists():
        continue
    for path in sorted(folder.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        js_ids = set(re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", text))
        html_ids = set(re.findall(r'id=["\']([^"\']+)["\']', text))
        missing = sorted(js_ids - html_ids)
        if missing:
            missing_any = True
            print(f"{role}/{path.name}: missing {missing}")

if not missing_any:
    print("All modal IDs present.")
