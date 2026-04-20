# Deployment — Sicherheits-Update

## 1. Token generieren + `/etc/pdf-anonymizer.env` anlegen

```bash
sudo tee /etc/pdf-anonymizer.env >/dev/null <<'EOF'
ANONYMIZER_API_TOKEN=qGStLJq50f_veiwIaojRXPsWR6B1fzShxkLcJyoEBCA
EOF
sudo chmod 600 /etc/pdf-anonymizer.env
sudo chown root:root /etc/pdf-anonymizer.env
```

## 2. Mapping-Verzeichnis anlegen

```bash
sudo mkdir -p /var/lib/anonymizer/mappings
sudo chmod 700 /var/lib/anonymizer
sudo chmod 700 /var/lib/anonymizer/mappings
sudo chown -R root:root /var/lib/anonymizer
```

## 3. Code deployen

```bash
# Neueste app.py nach /root/pdf_anonymizer/ bringen
sudo cp /home/claude-agent/pdf_anonymizer/app.py /root/pdf_anonymizer/app.py

# Optional: alte /tmp/anonymizer_mappings migrieren (PII!) — einmalig
if [ -d /tmp/anonymizer_mappings ]; then
    sudo mv /tmp/anonymizer_mappings/* /var/lib/anonymizer/mappings/ 2>/dev/null
    sudo rmdir /tmp/anonymizer_mappings
fi
```

## 4. systemd-Unit aktualisieren

```bash
sudo cp /home/claude-agent/pdf_anonymizer/systemd/pdf-anonymizer.service \
        /etc/systemd/system/pdf-anonymizer.service
sudo systemctl daemon-reload
sudo systemctl restart pdf-anonymizer
sudo systemctl status pdf-anonymizer
```

## 5. Test

```bash
# Ohne Token — muss 401/503 geben:
curl -i http://127.0.0.1:5050/anonymize-text -d '{"text":"Max Mustermann"}'

# Mit Token:
TOKEN=$(sudo grep ^ANONYMIZER_API_TOKEN /etc/pdf-anonymizer.env | cut -d= -f2)
curl -i http://127.0.0.1:5050/anonymize-text \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"text":"Max Mustermann wohnt in Berlin"}'
```

## 6. n8n-Workflow anpassen

Workflow "DGSVO Anonymizer" (`ggrEIvJy3sJRV05IZkH0i`) braucht jetzt im HTTP-Call den Header:
```
Authorization: Bearer {{ $credentials.anonymizerToken }}
```

Token als n8n-Credential "Anonymizer API" (Header Auth) anlegen.
