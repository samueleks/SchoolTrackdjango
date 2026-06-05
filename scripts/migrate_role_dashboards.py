"""Migra plantillas de dashboard por rol preservando modales y JS (UTF-8)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "login" / "Templates"

ROLES = {
    "administrativo": {
        "base": "base/dashboard_administrativo.html",
        "skip": {"materias_pdf.html"},
    },
    "maestro": {
        "base": "base/dashboard_maestro.html",
        "skip": set(),
    },
    "alumno": {
        "base": "base/dashboard_alumno.html",
        "skip": set(),
    },
}

SHARED_CSS_PATTERNS = [
    r"\.header-pro\s*\{[^}]+\}",
    r"\.logo-custom\s*\{[^}]+\}",
    r"\.nav-item\s*\{[^}]+\}",
    r"\.nav-item:hover\s*\{[^}]+\}",
    r"\.nav-item-active\s*\{[^}]+\}",
    r"\.nav-item-active i\s*\{[^}]+\}",
    r"\.nav-item i\s*\{[^}]+\}",
    r"\.sidebar-item-active\s*\{[^}]+\}",
    r"\.sidebar-item-active,\s*\.sidebar-item-active i\s*\{[^}]+\}",
    r"aside nav a\s*\{[^}]+\}",
    r"\.table-container\s*\{[^}]+\}",
]


def strip_shared_css(css: str) -> str:
    for pattern in SHARED_CSS_PATTERNS:
        css = re.sub(pattern, "", css, flags=re.S)
    css = re.sub(r"\n{3,}", "\n\n", css).strip()
    return css


def extract_main(text: str) -> str:
    match = re.search(r"<main[^>]*>(.*)</main>", text, flags=re.S | re.I)
    return match.group(1).strip() if match else ""


def extract_modals_before_layout(text: str) -> str:
    parts = []
    body_m = re.search(r"<body[^>]*>", text, flags=re.I)
    header_m = re.search(r"<header\s", text, flags=re.I)
    if body_m and header_m:
        before = text[body_m.end() : header_m.start()].strip()
        before = re.sub(r"^\s*<!--.*?-->\s*", "", before, flags=re.S)
        if before:
            parts.append(before)
    header_end = text.find("</header>")
    layout_m = re.search(r'<div class="layout-dashboard"', text, flags=re.I)
    if header_end != -1 and layout_m:
        after = text[header_end + len("</header>") : layout_m.start()].strip()
        if after:
            parts.append(after)
    return "\n\n".join(parts).strip()


def extract_post_main(text: str) -> str:
    end_main = text.lower().rfind("</main>")
    if end_main == -1:
        return ""
    tail = text[end_main + len("</main>") :]
    tail = re.sub(r"^\s*</div>\s*", "", tail, count=1)
    tail = re.sub(r"</body>.*", "", tail, flags=re.S | re.I)
    return tail.strip()


def split_tail(tail: str) -> tuple[str, str]:
    if not tail:
        return "", ""
    html_parts: list[str] = []
    script_parts: list[str] = []
    pos = 0
    for match in re.finditer(r"<script\b[^>]*>.*?</script>", tail, flags=re.S | re.I):
        chunk = tail[pos : match.start()].strip()
        if chunk:
            html_parts.append(chunk)
        script = match.group(0).strip()
        if len(script) > 40 and "gPressed" not in script and "toggleSidebar" not in script:
            script_parts.append(script)
        pos = match.end()
    rest = tail[pos:].strip()
    if rest:
        html_parts.append(rest)
    return "\n\n".join(html_parts).strip(), "\n\n".join(script_parts).strip()


def dedupe_scripts(scripts: str) -> str:
    found = re.findall(r"<script\b[^>]*>.*?</script>", scripts, flags=re.S | re.I)
    if len(found) <= 1:
        return scripts
    seen: set[str] = set()
    kept: list[str] = []
    for script in found:
        sig_match = re.search(r"(?:let|const|function)\s+(\w+)", script)
        signature = sig_match.group(1) if sig_match else script[:120]
        if signature in seen:
            continue
        seen.add(signature)
        kept.append(script.strip())
    return "\n\n".join(kept)


def extract_extra_head(text: str) -> str:
    head_m = re.search(r"<head>(.*?)</head>", text, flags=re.S | re.I)
    if not head_m:
        return ""
    lines = []
    for line in head_m.group(1).splitlines():
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
    match = re.search(r"<style>(.*?)</style>", text, flags=re.S | re.I)
    if not match:
        return ""
    css = strip_shared_css(match.group(1).strip())
    if not css:
        return ""
    return "{% block extra_css %}\n<style>\n" + css + "\n</style>\n{% endblock %}"


def extract_load_tags(text: str) -> list[str]:
    tags = []
    if "{% load custom_filters %}" in text:
        tags.append("{% load custom_filters %}")
    return tags


def extract_title(text: str) -> str:
    match = re.search(r"<title>(.*?)</title>", text, flags=re.I | re.S)
    return match.group(1).strip() if match else "SchoolTrack"


def extract_body_class(text: str) -> str:
    match = re.search(r'<body\s+class="([^"]*)"', text, flags=re.I)
    if not match:
        return ""
    classes = match.group(1)
    for remove in ("font-sans", "text-gray-800", "bg-gray-100"):
        classes = classes.replace(remove, "")
    return " ".join(classes.split())


def extract_main_class(text: str) -> str:
    match = re.search(r'<main\s+class="([^"]*)"', text, flags=re.I)
    if not match:
        return ""
    classes = match.group(1)
    for remove in ("flex-1", "dashboard-main", "bg-white", "admin-main", "overflow-y-auto", "p-12"):
        classes = classes.replace(remove, "")
    return " ".join(classes.split()).strip()


def migrate_file(path: Path, base_template: str) -> None:
    text = path.read_text(encoding="utf-8")
    if "{% extends" in text:
        print(f"  skip {path.name} (ya migrada)")
        return

    title = extract_title(text)
    load_tags = extract_load_tags(text)
    body_class = extract_body_class(text)
    main_class = extract_main_class(text)
    extra_head = extract_extra_head(text)
    page_css = extract_page_css(text)
    modals = extract_modals_before_layout(text)
    main = extract_main(text)
    post_html, post_js = split_tail(extract_post_main(text))
    extra_js = dedupe_scripts(post_js)

    parts = [f"{{% extends '{base_template}' %}}"]
    for tag in load_tags:
        parts.append(tag)
    parts.extend(["", f"{{% block title %}}{title}{{% endblock %}}"])

    if body_class:
        parts.append(f"{{% block body_class %}}{body_class}{{% endblock %}}")
    if main_class:
        parts.append(f"{{% block main_class %}}{main_class}{{% endblock %}}")
    if extra_head:
        parts.extend(["", "{% block extra_head %}", extra_head, "{% endblock %}"])
    if page_css:
        parts.extend(["", page_css])
    if modals:
        parts.extend(["", "{% block modals %}", modals, "{% endblock %}"])
    parts.extend(["", "{% block content %}", main, "{% endblock %}"])
    if post_html:
        parts.extend(["", "{% block page_modals %}", post_html, "{% endblock %}"])
    if extra_js:
        parts.extend(["", "{% block extra_js %}", extra_js, "{% endblock %}"])

    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"  migrated {path.name}")


def main():
    for role, config in ROLES.items():
        folder = ROOT / role
        print(f"Migrating {role}...")
        for path in sorted(folder.glob("*.html")):
            if path.name in config["skip"]:
                print(f"  skip {path.name}")
                continue
            migrate_file(path, config["base"])
    print("Done.")


if __name__ == "__main__":
    main()
