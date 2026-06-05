"""Limpia CSS roto y HTML duplicado del sidebar."""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "login" / "Templates"


def fix_css(content: str) -> str:
    # Fragmentos huérfanos tras borrar @media
    content = re.sub(
        r"\.sidebar\.open\s*\{[^}]+\}\s*"
        r"\.sidebar-overlay\s*\{[^}]+\}\s*"
        r"\.sidebar-overlay\.open\s*\{[^}]+\}\s*\}\s*",
        "",
        content,
        flags=re.S,
    )
    # Segundo bloque @media 640 duplicado (sidebar/header ya en dashboard-responsive.css)
    content = re.sub(
        r"\s*<style>\s*@media \(max-width: 640px\) \{\s*"
        r"\.header-pro[^}]+\}\s*"
        r"\.logo-custom[^}]+\}\s*"
        r"\.sidebar[^}]+\}\s*"
        r"\.sidebar-overlay[^}]+\}\s*\}\s*</style>\s*",
        "\n",
        content,
        flags=re.S,
    )
    return content


def fix_sidebar_html(content: str) -> str:
    # Patrón: aside + overlay + aside duplicado + overlay/nav
    content = re.sub(
        r"<aside class=\"sidebar[^\"]*\"[^>]*>\s*"
        r"<div class=\"sidebar-overlay\" onclick=\"toggleSidebar\(\)\"></div>"
        r"<aside class=\"sidebar[^\"]*\"[^>]*>"
        r"<div class=\"sidebar-overlay\" onclick=\"toggleSidebar\(\)\"></div>",
        '<aside class="sidebar lg:w-72 bg-gray-200 p-6 border-r border-gray-300 shadow-inner">\n            <div class="sidebar-overlay" onclick="toggleSidebar()"></div>',
        content,
        flags=re.S,
    )
    content = re.sub(
        r"<aside class=\"sidebar[^\"]*\"[^>]*>\s*"
        r"<div class=\"sidebar-overlay\" onclick=\"toggleSidebar\(\)\"></div>"
        r"<aside class=\"sidebar[^\"]*\"[^>]*><nav",
        '<aside class="sidebar lg:w-72 bg-gray-200 p-6 border-r border-gray-300 shadow-inner">\n            <div class="sidebar-overlay" onclick="toggleSidebar()"></div>\n            <nav',
        content,
        flags=re.S,
    )
    # Overlay antes del aside (fuera)
    content = re.sub(
        r"(<div class=\"flex layout-dashboard[^>]*>)\s*"
        r"<div class=\"sidebar-overlay\" onclick=\"toggleSidebar\(\)\"></div>\s*"
        r"(<aside class=\"sidebar)",
        r"\1\n        \2",
        content,
        flags=re.S,
    )
    # aside vacío seguido de overlay+aside (GestionUsuarios)
    content = re.sub(
        r"<aside class=\"sidebar lg:w-72[^>]*>\s*"
        r"<div class=\"sidebar-overlay\" onclick=\"toggleSidebar\(\)\"></div>"
        r"<aside class=\"sidebar lg:w-72[^>]*><nav",
        '<aside class="sidebar lg:w-72 bg-gray-200 p-6 border-r border-gray-300 shadow-inner bg-gradient-to-b from-gray-200 to-gray-300">\n            <div class="sidebar-overlay" onclick="toggleSidebar()"></div>\n            <nav',
        content,
        flags=re.S,
    )
    content = content.replace(' border-gray-300 sidebar"', ' border-gray-300"')
    return content


def main():
    for html in TEMPLATES.rglob("*.html"):
        if "includes" in html.parts:
            continue
        text = html.read_text(encoding="utf-8")
        new = fix_sidebar_html(fix_css(text))
        if new != text:
            html.write_text(new, encoding="utf-8")
            print(html.relative_to(TEMPLATES))


if __name__ == "__main__":
    main()
