# PDF Anonymizer

## Ueberblick

Flask-Service zur DSGVO-orientierten Anonymisierung von PDF-Dokumenten und Freitext. Das System extrahiert Text aus PDFs, sendet ihn an ein lokales Ollama-Modell und erzeugt eine anonymisierte PDF-Version inklusive Mapping-Tabelle.

## Zweck

- Personenbezogene Daten in deutschen Fachtexten anonymisieren
- PDF-Upload und Text-API in einem einfachen Service bereitstellen
- Ein lokales LLM ueber Ollama statt externer SaaS-Aufrufe nutzen

## Bestandteile

- `app.py`
  - Flask-API mit PDF- und Text-Endpunkten
- `run.sh`
  - Startskript mit Default-Umgebungsvariablen und Logging
- `systemhaus_anforderung.md`
  - Begleitdokument fuer Anforderungen und Einbettung
- `logs/`
  - Laufprotokolle

## Voraussetzungen

- Python 3.11+
- `flask`
- `pdfplumber`
- `fpdf`
- Lokaler oder erreichbarer Ollama-Server

## Einrichtung

```bash
cd /home/claude-agent/pdf_anonymizer
python3 -m venv .venv
source .venv/bin/activate
pip install flask pdfplumber fpdf
```

## Konfiguration

Per Umgebungsvariable:

```bash
OLLAMA_URL=http://172.28.0.20:11434
OLLAMA_MODEL=qwen2.5:7b
MAX_CHARS=8000
PORT=5050
```

## Nutzung

Service starten:

```bash
python3 app.py
```

oder

```bash
bash run.sh
```

Wichtige Endpunkte:

- `GET /health`
- `POST /anonymize`
- `POST /anonymize-text`

Beispiel Text-Anonymisierung:

```bash
curl -X POST http://127.0.0.1:5050/anonymize-text \
  -H 'Content-Type: application/json' \
  -d '{"text":"Max Mustermann wohnt in Berlin."}'
```

## Output

- Bei `/anonymize`: anonymisierte PDF-Datei als Download
- Bei `/anonymize-text`: JSON mit anonymisiertem Text und Mapping-Tabelle

## Betriebshinweise

- Die PDF-Erzeugung ersetzt bestimmte Unicode-Zeichen zur Kompatibilitaet mit Helvetica
- Sehr lange Texte werden auf `MAX_CHARS` gekuerzt
- Fuer gescannte PDFs ohne extrahierbaren Text liefert der Service bewusst einen Fehler statt OCR-Fallback

## Status

Lokaler Flask-Service fuer DSGVO-nahe Text- und PDF-Anonymisierung ueber Ollama.
