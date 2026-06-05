"""Inserta el script AJAX de búsqueda en GestionUsuarios si falta."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "login" / "Templates" / "administrador" / "GestionUsuarios.html"
GIT_REF = "1d15020"


def old_html() -> str:
    result = subprocess.run(
        ["git", "show", f"{GIT_REF}:login/Templates/administrador/GestionUsuarios.html"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


def extract_ajax_script(html: str) -> str:
    end_main = html.lower().find("</main>")
    tail = html[end_main + len("</main>") :]
    tail = re.sub(r"^\s*</div>\s*", "", tail, count=1)
    match = re.search(r"<script\b[^>]*>.*?</script>", tail, flags=re.S | re.I)
    if not match or "actualizarResultadosUsuarios" not in match.group(0):
        return ""
    script = match.group(0)
    # Quitar atajos de teclado duplicados (ya están en el layout base)
    script = re.sub(
        r"\n\s*// Atajos de teclado.*?</script>",
        "\n</script>",
        script,
        flags=re.S,
    )
    return script.strip()


def main():
    text = PATH.read_text(encoding="utf-8")
    if "actualizarResultadosUsuarios" in text:
        print("ajax already present")
        return
    ajax = extract_ajax_script(old_html())
    if not ajax:
        raise SystemExit("ajax script not found in git history")
    text = text.replace(
        "{% block extra_js %}\n",
        "{% block extra_js %}\n" + ajax + "\n\n",
        1,
    )
    PATH.write_text(text, encoding="utf-8")
    print("injected ajax into GestionUsuarios.html")


if __name__ == "__main__":
    main()
