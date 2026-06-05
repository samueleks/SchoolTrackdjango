"""Elimina bloques <script> duplicados en extra_js de plantillas administrador."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "login" / "Templates" / "administrador"


def dedupe_scripts(block: str) -> str:
    scripts = re.findall(r"<script\b[^>]*>.*?</script>", block, flags=re.S | re.I)
    if len(scripts) <= 1:
        return block
    seen: set[str] = set()
    kept: list[str] = []
    for script in scripts:
        sig_match = re.search(r"(?:let|const|function)\s+(\w+)", script)
        signature = sig_match.group(1) if sig_match else script[:120]
        if signature in seen:
            continue
        seen.add(signature)
        kept.append(script.strip())
    return "\n\n".join(kept) + "\n"


def main():
    for path in sorted(ROOT.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"({% block extra_js %})(.*?)({% endblock %})", text, flags=re.S)
        if not match:
            continue
        cleaned = dedupe_scripts(match.group(2))
        if cleaned == match.group(2):
            continue
        text = text[: match.start(2)] + "\n" + cleaned + text[match.end(2) :]
        path.write_text(text, encoding="utf-8")
        print(f"deduped {path.name}")


if __name__ == "__main__":
    main()
