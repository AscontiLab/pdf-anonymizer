#!/usr/bin/env python3
"""
DSGVO PDF-Anonymizer
POST /anonymize  — nimmt eine PDF, gibt anonymisierte PDF zurück
POST /anonymize-text — nimmt JSON {text}, gibt anonymisierten Text zurück (wie bisher)
"""

import io
import json
import logging
import os
import re
import textwrap
import urllib.request

import pdfplumber
from flask import Flask, jsonify, request, send_file
from fpdf import FPDF

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://172.28.0.20:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")  # 7b: ~2x schneller auf CPU, für Anonymisierung ausreichend
MAX_CHARS = int(os.environ.get("MAX_CHARS", "8000"))  # Reduziert für schnellere Verarbeitung (~5-8 Min statt 24)

SYSTEM_PROMPT = """\
Du bist ein DSGVO-Anonymisierungs-Assistent. Anonymisiere personenbezogene Daten \
in deutschen Fachtexten (insbesondere Steuer- und Rechtsdokumenten).

Ersetze IMMER:
- Personennamen → [NAME_A], [NAME_B], etc.
- Firmennamen → [FIRMA_A], [FIRMA_B], etc.
- Steuernummern / USt-IdNr. → [ST-XXXXX]
- Handelsregisternummern → [HRB-XXXXX]
- IBAN / Kontonummern → [IBAN-XXXXX]
- Adressen / Straßen → [ADRESSE_A]
- E-Mail-Adressen → [EMAIL_A]
- Telefonnummern → [TEL-XXXXX]
- Geburtsdaten → [DATUM_XXXXX]

Behalte IMMER bei:
- Rechtliche Paragraphen und Gesetze (z.B. §20 UmwStG)
- Fachbegriffe und Konzepte
- Beträge und Zahlen (außer direkt personenbezogen)
- Sachverhalte und Strukturen

Antworte NUR mit:
1. Dem anonymisierten Text
2. Darunter einer Mapping-Tabelle: Original → Platzhalter

Keine zusätzlichen Erklärungen.\
"""


def call_ollama(text: str) -> str:
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "stream": False,
        "keep_alive": "10m",  # Modell bleibt warm für Folgeanfragen
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Anonymisiere folgenden Text DSGVO-konform:\n\n{text}"},
        ],
    }).encode()

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:  # 10 Min Timeout für lange Texte
        data = json.loads(resp.read())
    return data["message"]["content"]


def extract_pdf_text(pdf_bytes: bytes) -> str:
    pages = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                pages.append(t)
    return "\n\n--- Seite ---\n\n".join(pages)


def split_mapping(full_response: str) -> tuple[str, str]:
    """Trennt anonymisierten Text von der Mapping-Tabelle."""
    parts = re.split(r"\n\n(?=\|)", full_response, maxsplit=1)
    anon_text = parts[0].strip()
    mapping = parts[1].strip() if len(parts) > 1 else ""
    return anon_text, mapping


def _ascii_safe(text: str) -> str:
    """Ersetzt Unicode-Sonderzeichen die Helvetica nicht kennt."""
    return (text
            .replace("→", "->")
            .replace("–", "-")
            .replace("—", "-")
            .replace("\u00e4", "ae").replace("\u00f6", "oe").replace("\u00fc", "ue")
            .replace("\u00c4", "Ae").replace("\u00d6", "Oe").replace("\u00dc", "Ue")
            .replace("\u00df", "ss")
            .encode("latin-1", errors="replace").decode("latin-1"))


def build_pdf(anon_text: str, mapping: str) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Titel
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "DSGVO-anonymisiertes Dokument", ln=True)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 6, "Erstellt mit DSGVO Anonymizer (Ollama / qwen2.5:7b)", ln=True)
    pdf.cell(0, 6, "Hinweis: Umlaute wurden fuer PDF-Kompatibilitaet ersetzt (ae/oe/ue)", ln=True)
    pdf.ln(4)

    # Anonymisierter Text
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Anonymisierter Text", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_fill_color(245, 245, 245)

    for line in anon_text.splitlines():
        wrapped = textwrap.wrap(_ascii_safe(line), width=100) if line.strip() else [""]
        for wline in wrapped:
            pdf.cell(0, 6, wline, ln=True)

    # Mapping-Tabelle
    if mapping:
        pdf.ln(6)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Mapping-Tabelle (Original -> Platzhalter)", ln=True)
        pdf.set_font("Courier", "", 9)
        for line in mapping.splitlines():
            wrapped = textwrap.wrap(_ascii_safe(line), width=110) if line.strip() else [""]
            for wline in wrapped:
                pdf.cell(0, 5, wline, ln=True)

    return pdf.output()


@app.route("/anonymize", methods=["POST"])
def anonymize_pdf():
    if "file" not in request.files:
        return jsonify({"error": "Kein 'file' im Request (multipart/form-data erwartet)"}), 400

    f = request.files["file"]
    if not f.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Nur PDF-Dateien erlaubt"}), 400

    pdf_bytes = f.read()
    app.logger.info("PDF empfangen: %s (%d Bytes)", f.filename, len(pdf_bytes))

    # Text extrahieren
    try:
        raw_text = extract_pdf_text(pdf_bytes)
    except Exception as e:
        return jsonify({"error": f"PDF-Textextraktion fehlgeschlagen: {e}"}), 500

    if not raw_text.strip():
        return jsonify({"error": "Kein Text in der PDF gefunden (evtl. gescannt?)"}), 422

    # Auf MAX_CHARS kürzen falls nötig
    if len(raw_text) > MAX_CHARS:
        app.logger.warning("Text zu lang (%d Zeichen) – wird auf %d gekürzt", len(raw_text), MAX_CHARS)
        raw_text = raw_text[:MAX_CHARS] + "\n\n[... Text gekürzt ...]"

    app.logger.info("Text extrahiert: %d Zeichen", len(raw_text))

    # Ollama anonymisieren
    try:
        response = call_ollama(raw_text)
    except Exception as e:
        return jsonify({"error": f"Ollama-Fehler: {e}"}), 502

    anon_text, mapping = split_mapping(response)
    app.logger.info("Anonymisiert: %d Zeichen", len(anon_text))

    # PDF generieren
    try:
        pdf_out = build_pdf(anon_text, mapping)
    except Exception as e:
        return jsonify({"error": f"PDF-Generierung fehlgeschlagen: {e}"}), 500

    stem = os.path.splitext(f.filename)[0]
    out_name = f"{stem}_anonymisiert.pdf"

    return send_file(
        io.BytesIO(pdf_out),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=out_name,
    )


@app.route("/anonymize-text", methods=["POST"])
def anonymize_text():
    data = request.get_json(silent=True)
    if not data or not data.get("text"):
        return jsonify({"error": "Kein 'text' im JSON-Body"}), 400

    text = data["text"][:MAX_CHARS]
    try:
        response = call_ollama(text)
    except Exception as e:
        return jsonify({"error": f"Ollama-Fehler: {e}"}), 502

    anon_text, mapping = split_mapping(response)
    return jsonify({
        "success": True,
        "anonymized_text": anon_text,
        "mapping_table": mapping,
        "original_length": len(data["text"]),
        "anonymized_length": len(anon_text),
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "model": OLLAMA_MODEL})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.logger.info("DSGVO PDF-Anonymizer startet auf Port %d", port)
    app.run(host="0.0.0.0", port=port)
