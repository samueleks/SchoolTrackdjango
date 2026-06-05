"""Corrige aside/overlay duplicados tras apply_responsive."""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "login" / "Templates"

# Overlay fuera + segundo <aside> pegado
BROKEN = re.compile(
    r'<aside class="sidebar[^>]*>\s*'
    r'<div class="sidebar-overlay" onclick="toggleSidebar\(\)"></div>'
    r'<aside class="sidebar[^>]*>',
    re.S,
)

OVERLAY_BEFORE_ASIDE = re.compile(
    r'(<div class="flex layout-dashboard[^>]*>)\s*'
    r'<div class="sidebar-overlay" onclick="toggleSidebar\(\)"></div>'
    r'(<aside class="sidebar[^>]*>)',
    re.S,
)

# aside con clase sidebar duplicada al final
ASIDE_DOUBLE_CLASS = re.compile(r' border-gray-300 sidebar"')


def clean_media(content: str) -> str:
    return re.sub(
        r"\s*/\* Responsive styles for mobile \*/\s*"
        r"@media \(max-width: 1024px\) \{[^}]+\}\s*",
        "\n",
        content,
        flags=re.S,
    )


def process(text: str) -> str:
    text = BROKEN.sub(
        '<aside class="sidebar lg:w-72 bg-gray-200 p-6 border-r border-gray-300 shadow-inner">\n'
        '            <div class="sidebar-overlay" onclick="toggleSidebar()"></div>\n',
        text,
    )
    # Variante con más clases en el aside roto
    text = re.sub(
        r'<aside class="sidebar lg:w-72[^>]*>\s*\n\s*'
        r'<div class="sidebar-overlay" onclick="toggleSidebar\(\)"></div>'
        r'<aside class="sidebar lg:w-72[^>]*>',
        lambda m: m.group(0).split("<aside")[0]
        + '<aside class="sidebar lg:w-72 bg-gray-200 p-6 border-r border-gray-300 shadow-inner">\n'
        '            <div class="sidebar-overlay" onclick="toggleSidebar()"></div>\n',
        text,
        flags=re.S,
    )
    # Caso GestionUsuarios: aside vacío + overlay + aside
    text = re.sub(
        r'<aside class="sidebar lg:w-72[^"]*"[^>]*>\s*'
        r'<div class="sidebar-overlay" onclick="toggleSidebar\(\)"></div>'
        r'<aside class="sidebar lg:w-72[^>]*>',
        '<aside class="sidebar lg:w-72 bg-gray-200 p-6 border-r border-gray-300 shadow-inner bg-gradient-to-b from-gray-200 to-gray-300">\n'
        '            <div class="sidebar-overlay" onclick="toggleSidebar()"></div>\n',
        text,
        flags=re.S,
    )
    text = OVERLAY_BEFORE_ASIDE.sub(r"\1\n        \2\n            <div class=\"sidebar-overlay\" onclick=\"toggleSidebar()\"></div>", text)
    text = ASIDE_DOUBLE_CLASS.sub(' border-gray-300"', text)
    # Segundo overlay dentro del mismo aside
    text = re.sub(
        r'(<div class="sidebar-overlay" onclick="toggleSidebar\(\)"></div>\s*){2,}',
        '<div class="sidebar-overlay" onclick="toggleSidebar()"></div>\n            ',
        text,
    )
    text = clean_media(text)
    return text


def main():
    for html in TEMPLATES.rglob("*.html"):
        if "includes" in html.parts:
            continue
        t = html.read_text(encoding="utf-8")
        n = process(t)
        if n != t:
            html.write_text(n, encoding="utf-8")
            print("fixed:", html.relative_to(TEMPLATES))


if __name__ == "__main__":
    main()
