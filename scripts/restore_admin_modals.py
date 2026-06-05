"""Restaura HTML y JS que quedó fuera de <main> al migrar plantillas administrador."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "login" / "Templates" / "administrador"
GIT_REF = "1d15020"


def old_template(name: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{GIT_REF}:login/Templates/administrador/{name}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


def extract_post_main(html: str) -> str:
    end_main = html.lower().find("</main>")
    if end_main == -1:
        return ""
    tail = html[end_main + len("</main>") :]
    tail = re.sub(r"^\s*</div>\s*", "", tail, count=1)
    tail = re.sub(r"</body>.*", "", tail, flags=re.S | re.I)
    return tail.strip()


def split_tail(tail: str) -> tuple[str, str]:
    html_parts: list[str] = []
    script_parts: list[str] = []
    pos = 0
    for match in re.finditer(r"<script\b[^>]*>.*?</script>", tail, flags=re.S | re.I):
        chunk = tail[pos : match.start()].strip()
        if chunk:
            html_parts.append(chunk)
        script = match.group(0).strip()
        if len(script) > 40 and "gPressed" not in script:
            script_parts.append(script)
        pos = match.end()
    rest = tail[pos:].strip()
    if rest:
        html_parts.append(rest)
    return "\n\n".join(html_parts).strip(), "\n\n".join(script_parts).strip()


def upsert_block(text: str, block_name: str, content: str) -> str:
    block = f"{{% block {block_name} %}}\n{content}\n{{% endblock %}}\n\n"
    pattern = rf"{{% block {block_name} %}}.*?{{% endblock %}}\s*"
    if re.search(pattern, text, flags=re.S):
        return re.sub(pattern, block, text, count=1, flags=re.S)
    anchor = "{% block extra_js %}"
    if anchor in text:
        return text.replace(anchor, block + anchor, 1)
    return text.rstrip() + "\n\n" + block


def merge_extra_js(text: str, scripts: str) -> str:
    if not scripts:
        return text
    marker = "{% block extra_js %}"
    if marker not in text:
        return text + f"\n{marker}\n{scripts}\n{{% endblock %}}\n"
    # Evitar duplicar si ya existe una función clave del bloque
    signature = None
    for name in (
        "actualizarResultadosUsuarios",
        "ejecutarAgregar",
        "ejecutarGuardar",
    ):
        if name in scripts:
            signature = name
            break
    if signature and signature in text:
        return text
    return text.replace(marker, f"{marker}\n{scripts}\n", 1)


def main():
    for path in sorted(TEMPLATES.glob("*.html")):
        tail = extract_post_main(old_template(path.name))
        if not tail:
            print(f"skip {path.name}")
            continue
        html_part, script_part = split_tail(tail)
        text = path.read_text(encoding="utf-8")
        if html_part:
            text = upsert_block(text, "page_modals", html_part)
        if script_part:
            text = merge_extra_js(text, script_part)
        path.write_text(text, encoding="utf-8")
        print(
            f"restored {path.name}: "
            f"html={len(html_part)} chars, js={len(script_part)} chars"
        )


if __name__ == "__main__":
    main()
