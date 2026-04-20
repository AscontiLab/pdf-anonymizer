#!/usr/bin/env python3
"""
DSGVO PDF-Anonymizer
POST /anonymize  — nimmt eine PDF, gibt anonymisierte PDF zurück
POST /anonymize-text — nimmt JSON {text}, gibt anonymisierten Text zurück (wie bisher)
"""

import hashlib
import io
import json
import logging
import os
import re
import subprocess
import tempfile
import textwrap
import urllib.request
from datetime import datetime
from typing import Any

import pdfplumber
from flask import Flask, jsonify, request, send_file
from fpdf import FPDF

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB Upload-Limit
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# API-Token für Zugriffskontrolle (Pflicht, außer /health).
# Ohne Token läuft der Dienst nur im ALLOW_NO_TOKEN-Modus (explizites Opt-in, nur fuer lokale Tests).
ANONYMIZER_API_TOKEN = os.environ.get("ANONYMIZER_API_TOKEN") or None
ALLOW_NO_TOKEN = os.environ.get("ANONYMIZER_ALLOW_NO_TOKEN", "").lower() in {"1", "true", "yes"}
if ANONYMIZER_API_TOKEN:
    if len(ANONYMIZER_API_TOKEN) < 24:
        raise RuntimeError("ANONYMIZER_API_TOKEN zu kurz (mind. 24 Zeichen erforderlich)")
    app.logger.info("ANONYMIZER_API_TOKEN konfiguriert — Endpoints geschützt")
elif ALLOW_NO_TOKEN:
    app.logger.warning("WARNUNG: ALLOW_NO_TOKEN=1 — Endpoints ungeschuetzt, nur fuer lokale Tests!")
else:
    raise RuntimeError(
        "ANONYMIZER_API_TOKEN nicht gesetzt. Setze ihn via Environment "
        "oder starte bewusst mit ANONYMIZER_ALLOW_NO_TOKEN=1 (nur lokal)."
    )

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")  # Qwen 2.5 7B: schnell, passt in 16GB RAM
MAX_CHARS = int(os.environ.get("MAX_CHARS", "8000"))
INCLUDE_MAPPING_IN_PDF = os.environ.get("INCLUDE_MAPPING_IN_PDF", "").lower() in {"1", "true", "yes"}
MAPPING_RETENTION_DAYS = int(os.environ.get("MAPPING_RETENTION_DAYS", "30"))


def _cleanup_old_mappings(directory: str, retention_days: int) -> None:
    """Loescht Mapping-JSONs aelter als retention_days. Laeuft lazy bei jedem Anonymize-Call."""
    if retention_days <= 0:
        return
    cutoff = datetime.now().timestamp() - retention_days * 86400
    try:
        for entry in os.listdir(directory):
            if not entry.startswith("mapping_") or not entry.endswith(".json"):
                continue
            path = os.path.join(directory, entry)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.unlink(path)
            except OSError:
                pass
    except OSError:
        pass

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

Antworte NUR als valides JSON in genau diesem Format:
{
  "anonymized_text": "vollstaendig anonymisierter Text",
  "mapping": [
    {"original": "Max Mustermann", "placeholder": "[NAME_A]"}
  ]
}

Keine Markdown-Tabelle, keine zusätzlichen Erklärungen.\
"""


def _mapping_to_text(mapping: list[dict[str, str]]) -> str:
    if not mapping:
        return ""
    lines = ["| Original | Platzhalter |", "|---|---|"]
    for item in mapping:
        original = item.get("original", "").replace("\n", " ").strip()
        placeholder = item.get("placeholder", "").replace("\n", " ").strip()
        if original and placeholder:
            lines.append(f"| {original} | {placeholder} |")
    return "\n".join(lines)


def _extract_json_object(raw_response: str) -> dict[str, Any]:
    raw_response = raw_response.strip()
    try:
        data = json.loads(raw_response)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", raw_response, flags=re.DOTALL)
    if not match:
        raise ValueError("Modellantwort ist kein valides JSON-Objekt")

    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("Modellantwort enthaelt kein JSON-Objekt")
    return data


def _normalize_ollama_response(raw_response: str) -> tuple[str, list[dict[str, str]]]:
    data = _extract_json_object(raw_response)
    anonymized_text = data.get("anonymized_text", "")
    if not isinstance(anonymized_text, str) or not anonymized_text.strip():
        raise ValueError("JSON-Antwort enthaelt keinen anonymisierten Text")

    raw_mapping = data.get("mapping", [])
    mapping: list[dict[str, str]] = []
    if isinstance(raw_mapping, list):
        for item in raw_mapping:
            if not isinstance(item, dict):
                continue
            original = item.get("original")
            placeholder = item.get("placeholder")
            if isinstance(original, str) and isinstance(placeholder, str) and original.strip() and placeholder.strip():
                mapping.append({
                    "original": original.strip(),
                    "placeholder": placeholder.strip(),
                })

    return anonymized_text.strip(), mapping


def call_ollama(text: str, existing_mapping: list[dict[str, str]] | None = None) -> tuple[str, list[dict[str, str]]]:
    # Kontext aus vorherigen Chunks mitgeben fuer konsistente Platzhalter
    mapping_context = ""
    if existing_mapping:
        mapping_lines = ", ".join(
            f'"{m["original"]}" -> {m["placeholder"]}' for m in existing_mapping
        )
        mapping_context = (
            f"\n\nWICHTIG: Verwende exakt dieselben Platzhalter fuer bereits bekannte Entitaeten:\n"
            f"{mapping_lines}\n"
            f"Neue Entitaeten bekommen den naechsten freien Buchstaben/Nummer."
        )

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "stream": False,
        "keep_alive": "10m",  # Modell bleibt warm für Folgeanfragen
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Anonymisiere folgenden Text DSGVO-konform:{mapping_context}\n\n{text}"},
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
    return _normalize_ollama_response(data["message"]["content"])


def extract_pdf_text(pdf_bytes: bytes) -> tuple[str, bool]:
    """Extrahiert Text aus PDF. Fallback auf OCR bei gescannten Dokumenten.
    Gibt (text, used_ocr) zurueck."""
    pages = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                pages.append(t)

    if pages:
        return "\n\n--- Seite ---\n\n".join(pages), False

    # Fallback: OCR mit ocrmypdf + tesseract (deutsch + englisch)
    app.logger.info("Kein Text gefunden — starte OCR-Erkennung...")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_in:
        tmp_in.write(pdf_bytes)
        tmp_in_path = tmp_in.name

    tmp_out_path = tmp_in_path.replace(".pdf", "_ocr.pdf")
    try:
        subprocess.run(
            ["ocrmypdf", "--force-ocr", "-l", "deu+eng", "--skip-big", "50",
             "--jobs", "2", tmp_in_path, tmp_out_path],
            capture_output=True, text=True, timeout=300, check=True,
        )
        ocr_pages = []
        with pdfplumber.open(tmp_out_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    ocr_pages.append(t)
        if ocr_pages:
            app.logger.info("OCR erfolgreich: %d Seiten erkannt", len(ocr_pages))
            return "\n\n--- Seite ---\n\n".join(ocr_pages), True
        return "", True
    except subprocess.TimeoutExpired:
        app.logger.error("OCR-Timeout nach 5 Minuten")
        return "", True
    except subprocess.CalledProcessError as e:
        app.logger.error("OCR fehlgeschlagen: %s", e.stderr)
        return "", True
    finally:
        for p in (tmp_in_path, tmp_out_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def chunk_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for block in re.split(r"(\n\n--- Seite ---\n\n|\n\n)", text):
        if not block:
            continue
        if len(block) > max_chars:
            for i in range(0, len(block), max_chars):
                part = block[i:i + max_chars]
                if current:
                    chunks.append("".join(current).strip())
                    current = []
                    current_len = 0
                chunks.append(part.strip())
            continue
        if current_len + len(block) > max_chars and current:
            chunks.append("".join(current).strip())
            current = []
            current_len = 0
        current.append(block)
        current_len += len(block)

    if current:
        chunks.append("".join(current).strip())

    return [chunk for chunk in chunks if chunk]


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
    pdf.cell(0, 6, "Erstellt mit DSGVO Anonymizer (Ollama / Gemma 4)", ln=True)
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

    # Mapping-Tabelle ist standardmaessig deaktiviert, damit die PDF anonym bleibt.
    if mapping and INCLUDE_MAPPING_IN_PDF:
        pdf.ln(6)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Mapping-Tabelle (Original -> Platzhalter)", ln=True)
        pdf.set_font("Courier", "", 9)
        for line in mapping.splitlines():
            wrapped = textwrap.wrap(_ascii_safe(line), width=110) if line.strip() else [""]
            for wline in wrapped:
                pdf.cell(0, 5, wline, ln=True)

    return pdf.output()


def _check_auth():
    """Prüft Bearer-Token. Gibt None zurück wenn OK, sonst eine JSON-Response."""
    if not ANONYMIZER_API_TOKEN:
        if ALLOW_NO_TOKEN:
            return None
        return jsonify({"error": "Service not configured (no token)"}), 503
    import hmac
    auth = request.headers.get("Authorization", "")
    if hmac.compare_digest(auth, f"Bearer {ANONYMIZER_API_TOKEN}"):
        return None
    return jsonify({"error": "Unauthorized"}), 401


@app.route("/anonymize", methods=["POST"])
def anonymize_pdf():
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    if "file" not in request.files:
        return jsonify({"error": "Kein 'file' im Request (multipart/form-data erwartet)"}), 400

    f = request.files["file"]
    if not f.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Nur PDF-Dateien erlaubt"}), 400

    pdf_bytes = f.read()
    app.logger.info("PDF empfangen: %s (%d Bytes)", f.filename, len(pdf_bytes))

    # Text extrahieren (mit OCR-Fallback fuer gescannte PDFs)
    try:
        raw_text, used_ocr = extract_pdf_text(pdf_bytes)
    except Exception as e:
        return jsonify({"error": f"PDF-Textextraktion fehlgeschlagen: {e}"}), 500

    if not raw_text.strip():
        return jsonify({"error": "Kein Text in der PDF gefunden — auch OCR konnte keinen Text erkennen"}), 422

    app.logger.info("Text extrahiert: %d Zeichen%s", len(raw_text), " (via OCR)" if used_ocr else "")

    chunks = chunk_text(raw_text, MAX_CHARS)
    app.logger.info("Verarbeite %d Chunk(s) mit max. %d Zeichen", len(chunks), MAX_CHARS)

    # Ollama anonymisieren — mit Mapping-Kontext fuer chunk-uebergreifende Konsistenz
    try:
        anonymized_chunks = []
        combined_mapping: list[dict[str, str]] = []
        for index, chunk in enumerate(chunks, start=1):
            app.logger.info("Anonymisiere Chunk %d/%d (%d bekannte Mappings)", index, len(chunks), len(combined_mapping))
            anon_text, mapping_entries = call_ollama(chunk, existing_mapping=combined_mapping or None)
            anonymized_chunks.append(anon_text)
            # Nur neue Mappings hinzufuegen (Duplikate vermeiden)
            known_originals = {m["original"] for m in combined_mapping}
            for entry in mapping_entries:
                if entry["original"] not in known_originals:
                    combined_mapping.append(entry)
                    known_originals.add(entry["original"])
    except Exception as e:
        return jsonify({"error": f"Ollama-Fehler: {e}"}), 502

    anon_text = "\n\n".join(anonymized_chunks)

    # Deterministisches Post-Processing: Alle bekannten Originale nochmal ersetzen
    # (faengt Faelle ab, in denen das LLM ein Original uebersehen hat)
    for entry in combined_mapping:
        anon_text = anon_text.replace(entry["original"], entry["placeholder"])

    mapping = _mapping_to_text(combined_mapping)
    app.logger.info("Anonymisiert: %d Zeichen, %d Mappings", len(anon_text), len(combined_mapping))

    # PDF generieren
    try:
        pdf_out = build_pdf(anon_text, mapping)
    except Exception as e:
        return jsonify({"error": f"PDF-Generierung fehlgeschlagen: {e}"}), 500

    # Mapping als JSON speichern (fuer spaetere De-Anonymisierung).
    # Default-Pfad ist /var/lib/anonymizer/mappings mit chmod 700 — enthaelt PII!
    mapping_json_path = None
    if combined_mapping:
        mapping_dir = os.environ.get("MAPPING_DIR", "/var/lib/anonymizer/mappings")
        os.makedirs(mapping_dir, exist_ok=True)
        try:
            os.chmod(mapping_dir, 0o700)
        except PermissionError:
            app.logger.warning("Konnte Berechtigung auf %s nicht auf 700 setzen", mapping_dir)
        _cleanup_old_mappings(mapping_dir, retention_days=MAPPING_RETENTION_DAYS)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_hash = hashlib.sha256(pdf_bytes).hexdigest()[:8]
        mapping_json_path = os.path.join(mapping_dir, f"mapping_{ts}_{file_hash}.json")
        mapping_data = {
            "source_file": f.filename,
            "timestamp": ts,
            "model": OLLAMA_MODEL,
            "mapping": combined_mapping,
        }
        with open(mapping_json_path, "w", encoding="utf-8") as mf:
            json.dump(mapping_data, mf, ensure_ascii=False, indent=2)
        os.chmod(mapping_json_path, 0o600)
        app.logger.info("Mapping gespeichert: %s (%d Eintraege)", mapping_json_path, len(combined_mapping))

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
    auth_err = _check_auth()
    if auth_err:
        return auth_err
    data = request.get_json(silent=True)
    if not data or not data.get("text"):
        return jsonify({"error": "Kein 'text' im JSON-Body"}), 400

    raw_text = data["text"]
    chunks = chunk_text(raw_text, MAX_CHARS)
    try:
        anonymized_chunks = []
        combined_mapping: list[dict[str, str]] = []
        for chunk in chunks:
            anon_chunk, mapping_entries = call_ollama(chunk, existing_mapping=combined_mapping or None)
            anonymized_chunks.append(anon_chunk)
            known_originals = {m["original"] for m in combined_mapping}
            for entry in mapping_entries:
                if entry["original"] not in known_originals:
                    combined_mapping.append(entry)
                    known_originals.add(entry["original"])
    except Exception as e:
        return jsonify({"error": f"Ollama-Fehler: {e}"}), 502

    anon_text = "\n\n".join(anonymized_chunks)
    # Deterministisches Post-Processing
    for entry in combined_mapping:
        anon_text = anon_text.replace(entry["original"], entry["placeholder"])

    mapping = _mapping_to_text(combined_mapping)
    return jsonify({
        "success": True,
        "anonymized_text": anon_text,
        "mapping_table": mapping,
        "mapping_entries": combined_mapping,
        "original_length": len(raw_text),
        "anonymized_length": len(anon_text),
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "model": OLLAMA_MODEL})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.logger.info("DSGVO PDF-Anonymizer startet auf Port %d", port)
    app.run(host="0.0.0.0", port=port)
