"""Migra plantillas administrador a base/dashboard_administrador.html (UTF-8)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "login" / "Templates" / "administrador"

SHARED_CSS_PATTERNS = [
    r"\.header-pro\s*\{[^}]+\}",
    r"\.logo-custom\s*\{[^}]+\}",
    r"\.nav-item\s*\{[^}]+\}",
    r"\.nav-item:hover\s*\{[^}]+\}",
    r"\.nav-item-active\s*\{[^}]+\}",
    r"\.nav-item-active i\s*\{[^}]+\}",
    r"\.sidebar-item\s*\{[^}]+\}",
    r"\.sidebar-item:hover\s*\{[^}]+\}",
    r"\.table-container\s*\{[^}]+\}",
]


def strip_shared_css(css: str) -> str:
    for pattern in SHARED_CSS_PATTERNS:
        css = re.sub(pattern, "", css, flags=re.S)
    css = re.sub(r"\n{3,}", "\n\n", css).strip()
    return css


def extract_between(text: str, start: str, end: str) -> str:
    i = text.find(start)
    if i == -1:
        return ""
    j = text.find(end, i + len(start))
    if j == -1:
        return ""
    return text[i + len(start):j]


def extract_main(text: str) -> str:
    m = re.search(
        r"<main[^>]*>(.*)</main>",
        text,
        flags=re.S | re.I,
    )
    return m.group(1).strip() if m else ""


def extract_modals(text: str) -> str:
    parts = []
    body_m = re.search(r"<body[^>]*>", text, flags=re.I)
    header_m = re.search(r"<header\s", text, flags=re.I)
    if body_m and header_m:
        before = text[body_m.end():header_m.start()].strip()
        before = re.sub(r"^\s*<!--.*?-->\s*", "", before, flags=re.S)
        if before:
            parts.append(before)
    header_end = text.find("</header>")
    layout_m = re.search(r'<div class="layout-dashboard"', text, flags=re.I)
    if header_end != -1 and layout_m:
        after = text[header_end + len("</header>"):layout_m.start()].strip()
        if after:
            parts.append(after)
    return "\n\n".join(parts)


def extract_extra_js(text: str) -> str:
    main_end = text.rfind("</main>")
    if main_end == -1:
        return ""
    tail = text[main_end + len("</main>"):]
    tail = re.sub(r"</div>\s*</body>.*", "", tail, flags=re.S | re.I)
    scripts = []
    for m in re.finditer(r"<script[^>]*>.*?</script>", tail, flags=re.S | re.I):
        script = m.group(0).strip()
        if "gPressed" in script or len(script) < 20:
            continue
        scripts.append(script)
    return "\n\n".join(scripts).strip()


def extract_extra_head(text: str) -> str:
    head = extract_between(text, "<head>", "</head>")
    lines = []
    for line in head.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(x in stripped for x in (
            "<meta", "<title", "tailwindcss", "font-awesome",
            "dashboard_responsive", "<style", "</style>", "<script src="
        )):
            continue
        if stripped.startswith("<link"):
            lines.append(line)
    return "\n".join(lines).strip()


def extract_page_css(text: str) -> str:
    m = re.search(r"<style>(.*?)</style>", text, flags=re.S | re.I)
    if not m:
        return ""
    css = strip_shared_css(m.group(1).strip())
    if not css:
        return ""
    return "{% block extra_css %}\n<style>\n" + css + "\n</style>\n{% endblock %}"


def extract_load_tags(text: str) -> str:
    tags = []
    if "{% load static %}" in text:
        tags.append("{% load static %}")
    if "{% load custom_filters %}" in text:
        tags.append("{% load custom_filters %}")
    return "\n".join(tags)


def extract_title(text: str) -> str:
    m = re.search(r"<title>(.*?)</title>", text, flags=re.I | re.S)
    return m.group(1).strip() if m else "SchoolTrack"


def extract_body_class(text: str) -> str:
    m = re.search(r"<body\s+class=\"([^\"]*)\"", text, flags=re.I)
    if not m:
        return ""
    classes = m.group(1).replace("font-sans", "").replace("text-gray-800", "").strip()
    return classes


def migrate_file(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    title = extract_title(text)
    load_tags = extract_load_tags(text)
    body_class = extract_body_class(text)
    extra_head = extract_extra_head(text)
    page_css = extract_page_css(text)
    modals = extract_modals(text)
    main = extract_main(text)
    extra_js = extract_extra_js(text)

    parts = [
        load_tags,
        "{% extends 'base/dashboard_administrador.html' %}",
        "",
        f"{{% block title %}}{title}{{% endblock %}}",
    ]

    if body_class and body_class != "bg-gray-100":
        parts.append(f"{{% block body_class %}}{body_class}{{% endblock %}}")
    elif body_class == "bg-gray-100":
        parts.append("{% block body_class %}bg-gray-100{% endblock %}")

    if extra_head:
        parts.extend(["", "{% block extra_head %}", extra_head, "{% endblock %}"])

    if page_css:
        parts.extend(["", page_css])

    if modals:
        parts.extend(["", "{% block modals %}", modals, "{% endblock %}"])

    parts.extend([
        "",
        "{% block content %}",
        main,
        "{% endblock %}",
    ])

    if extra_js:
        parts.extend([
            "",
            "{% block extra_js %}",
            extra_js,
            "{% endblock %}",
        ])

    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"  migrated {path.name}")


def main():
    files = sorted(ROOT.glob("*.html"))
    print(f"Migrating {len(files)} templates...")
    for path in files:
        migrate_file(path)
    print("Done.")


if __name__ == "__main__":
    main()
