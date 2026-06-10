"""Verifica que tailwind.css compilado incluya clases críticas."""
from pathlib import Path

css = Path('login/static/css/tailwind.css').read_text(encoding='utf-8')
# En CSS minificado Tailwind escapa : / [ ] como \: \/ \[ \]
needles = [
    'bg-blue-600',
    'bg-blue-50',
    'z-\\[120\\]',
    'bg-black\\/50',
    'text-\\[10px\\]',
    'min-h-\\[calc',
    'focus\\:ring-\\[\\#2b63d9\\]',
    'last\\:border-0',
    'opacity-50',
    'text-amber-700\\/70',
]
missing = [n for n in needles if n not in css]
if missing:
    print('MISSING:', ', '.join(missing))
    raise SystemExit(1)
print(f'OK: {len(needles)} clases críticas presentes ({len(css) // 1024} KB)')
