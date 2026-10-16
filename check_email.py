"""SMTP connection diagnostics only; never sends a message or prints secrets."""
import json
import os
import smtplib
import ssl

results = []
host = os.environ.get('SMTP_HOST') or 'smtp.gmail.com'
username = os.environ.get('SMTP_USERNAME')
password = os.environ.get('SMTP_PASSWORD')
if not username or not password:
    raise SystemExit('SMTP_USERNAME or SMTP_PASSWORD is missing')
for port in [465, 587]:
    stage = 'connect'
    try:
        if port == 465:
            smtp = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(host, port, timeout=20)
            stage = 'starttls'
            smtp.starttls(context=ssl.create_default_context())
        with smtp:
            stage = 'authenticate'
            smtp.login(username, password)
            results.append({'port': port, 'stage': stage, 'result': 'authenticated; no message sent'})
        break
    except Exception as error:
        results.append({'port': port, 'stage': stage, 'error': type(error).__name__})
        if stage == 'authenticate':
            break
print(json.dumps(results))

