"""Coloca {% extends %} como primera etiqueta en plantillas administrador."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "login" / "Templates" / "administrador"
EXTENDS = "{% extends 'base/dashboard_administrador.html' %}"

for path in ROOT.glob("*.html"):
    text = path.read_text(encoding="utf-8")
    loads = [l for l in re.findall(r"{% load [\w_]+ %}", text) if l != "{% load static %}"]
    rest = text
    for token in re.findall(r"{% (?:load [\w_]+|extends [^%]+) %}\s*", text):
        rest = rest.replace(token, "", 1)
    rest = rest.strip()
    new = EXTENDS + "\n"
    for load in loads:
        new += load + "\n"
    new += "\n" + rest + "\n"
    path.write_text(new, encoding="utf-8")
    print(f"fixed {path.name}")
