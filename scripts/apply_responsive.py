"""Aplica includes y clases responsive en plantillas dashboard."""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "login" / "Templates"
INCLUDE_SNIPPET = "{% include 'includes/dashboard_responsive.html' %}"
SKIP = {"includes", "materias_pdf.html", "cambiar_contrasena.html"}


def ensure_load_static(content: str) -> str:
    if "{% load static %}" in content:
        return content
    if content.lstrip().startswith("<!DOCTYPE"):
        return re.sub(
            r"(<!DOCTYPE[^>]*>\s*\n)(<html)",
            r"\1{% load static %}\n\2",
            content,
            count=1,
            flags=re.I,
        )
    return "{% load static %}\n" + content


def add_include(content: str) -> str:
    if INCLUDE_SNIPPET in content:
        return content
    marker = "font-awesome"
    idx = content.find(marker)
    if idx == -1:
        return content
    line_end = content.find("\n", idx)
    if line_end == -1:
        return content
    insert_at = line_end + 1
    return content[:insert_at] + f"    {INCLUDE_SNIPPET}\n" + content[insert_at:]


def remove_sidebar_media_blocks(content: str) -> str:
    content = re.sub(
        r"\s*/\* Responsive styles for mobile \*/\s*"
        r"@media \(max-width: 1024px\) \{[^}]+\}\s*",
        "\n",
        content,
        flags=re.S,
    )
    content = re.sub(
        r"\s*<style>\s*@media \(max-width: 640px\) \{\s*"
        r"\.header-pro[^}]+\}\s*</style>\s*",
        "\n",
        content,
        flags=re.S,
    )
    return content


def fix_aside(content: str) -> str:
    content = re.sub(
        r'<aside class="w-full lg:w-72',
        '<aside class="sidebar lg:w-72',
        content,
    )
    content = re.sub(
        r'<aside class="sidebar w-full lg:w-72',
        '<aside class="sidebar lg:w-72',
        content,
    )

    def add_overlay(m):
        tag = m.group(0)
        rest = m.group(1)
        if "sidebar-overlay" in rest[:200]:
            return m.group(0)
        return tag + '\n            <div class="sidebar-overlay" onclick="toggleSidebar()"></div>' + rest

    content = re.sub(
        r"(<aside class=\"sidebar[^\"]*\"[^>]*>)(\s*)",
        add_overlay,
        content,
        count=1,
    )
    return content


def fix_layout_header_main(content: str) -> str:
    content = content.replace(
        'class="flex min-h-[calc(100vh-90px)]"',
        'class="flex layout-dashboard min-h-0"',
    )
    content = content.replace(
        'class="flex min-h-[calc(100svh-90px)]"',
        'class="flex layout-dashboard min-h-0"',
    )
    content = re.sub(
        r'class="header-pro([^"]*) px-10',
        r'class="header-pro\1 px-4 md:px-10',
        content,
    )
    content = re.sub(
        r'(<span class=")([^"]*text-xl[^"]*">Bienvenido)',
        r'\1header-welcome \2',
        content,
    )
    content = re.sub(
        r'(<span class=")(text-xl font-medium">Bienvenido)',
        r'\1header-welcome \2',
        content,
    )
    content = content.replace('class="flex-1 p-10 ', 'class="flex-1 dashboard-main ')
    content = content.replace('class="flex-1 p-8 ', 'class="flex-1 dashboard-main ')
    content = re.sub(
        r'(<h2 class=")(text-4xl font-bold)',
        r'\1page-title \2',
        content,
    )
    content = re.sub(
        r'(<h2 class=")(text-3xl font-bold)',
        r'\1page-title \2',
        content,
    )
    content = content.replace(
        'class="table-container show',
        'class="table-container table-scroll show',
    )
    content = content.replace(
        'class="table-container bg-white',
        'class="table-container table-scroll bg-white',
    )
    content = re.sub(
        r'class="flex gap-3"([^>]*>)\s*\n\s*<a href="{% url \'exportar',
        r'class="flex flex-wrap gap-3 btn-stack-mobile"\1\n                    <a href="{% url \'exportar',
        content,
        count=1,
    )
    return content


def remove_toggle_sidebar_fn(content: str) -> str:
    return re.sub(
        r"\s*function toggleSidebar\(\)\s*\{[^}]+\}\s*",
        "\n",
        content,
        flags=re.S,
    )


def process_file(path: Path) -> bool:
    if any(s in path.as_posix() for s in SKIP):
        return False
    text = path.read_text(encoding="utf-8")
    if "header-pro" not in text and "<aside" not in text:
        return False
    original = text
    text = ensure_load_static(text)
    text = add_include(text)
    text = remove_sidebar_media_blocks(text)
    text = fix_aside(text)
    text = fix_layout_header_main(text)
    text = remove_toggle_sidebar_fn(text)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def main():
    changed = []
    for html in TEMPLATES.rglob("*.html"):
        if process_file(html):
            changed.append(html.relative_to(TEMPLATES))
    print(f"Updated {len(changed)} files:")
    for p in changed:
        print(f"  - {p}")


if __name__ == "__main__":
    main()
