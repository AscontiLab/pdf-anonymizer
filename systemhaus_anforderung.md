# Anforderung: Azure App Registration für OneDrive-Integration

## Hintergrund
Wir möchten einen automatisierten DSGVO-Anonymisierungsprozess einrichten.
PDFs werden in einem OneDrive-Ordner abgelegt, automatisch anonymisiert und
in einem zweiten OneDrive-Ordner wieder bereitgestellt.

Die Verarbeitung erfolgt auf unserem eigenen Server (keine Cloud-Dienste außer Microsoft).

---

## Was wir benötigen

### 1. Azure App Registration
Bitte legt im Azure Active Directory unseres Tenants eine App Registration an:

- **Name:** `n8n-OneDrive-Anonymizer`
- **Unterstützte Kontotypen:** Nur Konten in diesem Organisationsverzeichnis (Single Tenant)
- **Redirect URI (Web):** `https://agents.umzwei.de/rest/oauth2-credential/callback`

### 2. API-Berechtigungen
Bitte folgende **delegierte Berechtigung** (nicht Application) über Microsoft Graph hinzufügen:

| Berechtigung | Typ | Zweck |
|---|---|---|
| `Files.ReadWrite` | Delegiert | PDFs aus OneDrive lesen und anonymisierte PDFs zurückschreiben |

### 3. Admin-Zustimmung
Bitte die Admin-Zustimmung für die oben genannte Berechtigung erteilen.

### 4. Client Secret
Bitte ein Client Secret erstellen (Laufzeit: 24 Monate) und uns folgende Werte mitteilen:
- Client ID (Anwendungs-ID)
- Tenant ID (Verzeichnis-ID)
- Client Secret (Wert)

---

## Sicherheitshinweise
- Die App hat ausschließlich Zugriff auf OneDrive-Dateien des autorisierten Nutzers
- Keine Berechtigungen auf E-Mails, Kalender oder andere Dienste
- Der Server liegt in unserem eigenen Rechenzentrum
- Es werden keine Daten an externe KI-Dienste übertragen (Verarbeitung lokal via Ollama)

---

## Wir haben bereits
Eine App Registration mit folgenden Daten angelegt — es fehlt nur noch die Admin-Zustimmung:

- **Anwendungs-ID:** `48bcfda0-c27c-485a-9565-9ec39eb28435`
- **Verzeichnis-ID:** `5382273d-f770-47a0-bb9e-ac30d017db5a`
- **Redirect URI:** bereits eingetragen
- **Berechtigung:** `Files.ReadWrite` bereits hinzugefügt

**Es fehlt nur noch: Admin-Zustimmung erteilen** (im Azure Portal unter der App → API-Berechtigungen → "Administratorzustimmung erteilen").
