"""Actualiza clase layout-dashboard preservando UTF-8."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "login" / "Templates"
OLD = 'class="flex layout-dashboard min-h-0"'
NEW = 'class="layout-dashboard"'

changed = []
for path in ROOT.rglob("*.html"):
    text = path.read_text(encoding="utf-8")
    if OLD not in text:
        continue
    path.write_text(text.replace(OLD, NEW), encoding="utf-8")
    changed.append(path.relative_to(ROOT))

print(f"updated {len(changed)} files")
for p in changed:
    print(f"  - {p}")
