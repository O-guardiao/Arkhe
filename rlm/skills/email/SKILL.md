+++
name = "email"
description = "Send and read emails via SMTP/IMAP using Python stdlib. Supports Gmail, Outlook, and any SMTP provider. Use when: user asks to enviar email, ler emails, pesquisar na caixa de entrada, ou responder mensagens. NOT for: calendário (use calendar skill), Slack/Discord (skills dedicadas)."
tags = ["email", "e-mail", "gmail", "outlook", "smtp", "imap", "enviar email", "ler email", "caixa de entrada", "mensagem", "correio"]
priority = "contextual"

[sif]
signature = "email_send(to: str, subject: str, body: str, from_addr: str = '') -> str"
prompt_hint = "Use para enviar ou responder e-mail formal, resumo, convite ou follow-up para destinatários específicos."
short_sig = "email_send(to,subj,body)"
compose = ["calendar", "notion", "shell"]
examples_min = ["enviar e-mail de follow-up com assunto e corpo definidos"]
impl = """
def email_send(to, subject, body, from_addr=''):
    import smtplib, os
    from email.message import EmailMessage
    msg = EmailMessage()
    msg['To'] = to
    msg['From'] = from_addr or os.environ.get('EMAIL_FROM', '')
    msg['Subject'] = subject
    msg.set_content(body)
    host = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
    port = int(os.environ.get('SMTP_PORT', '587'))
    user = os.environ.get('SMTP_USER', '')
    pwd = os.environ.get('SMTP_PASS', '')
    with smtplib.SMTP(host, port) as s:
        s.starttls()
        if user:
            s.login(user, pwd)
        s.send_message(msg)
    return f'Email enviado para {to}'
"""

[runtime]
estimated_cost = 0.9
risk_level = "high"
side_effects = ["external_message_send"]
preconditions = ["env_any:SMTP_USER|EMAIL_USER", "env_any:SMTP_PASS|EMAIL_APP_PASSWORD"]
postconditions = ["email_sent_or_draft_prepared"]
fallback_policy = "draft_only_or_ask_user"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "email smtp imap inbox send reply message subject recipients"
example_queries = ["envie um e-mail para o cliente", "responda esta mensagem por e-mail"]

[requires]
bins = []
+++

# Email Skill

Envio e leitura de emails via stdlib Python (`smtplib`, `imaplib`, `email`).

## Quando usar

✅ **USE quando:**
- "Envia email para fulano@example.com"
- "Lê meus últimos emails"
- "Responde o email sobre X"
- "Encaminha este email para..."
- "Verifica se chegou confirmação de..."

❌ **NÃO use quando:**
- Calendário e agendamentos → use `calendar` skill
- Notificações Telegram → use `telegram_bot` skill
- Email em massa / marketing → use ESP dedicado (Mailchimp/Resend)

## Configuração Gmail

```python
import os

# Gmail — requer App Password (não a senha da conta)
# Gera em: https://myaccount.google.com/apppasswords
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_USER = os.environ.get("EMAIL_USER", "")       # seu@gmail.com
EMAIL_PASS = os.environ.get("EMAIL_APP_PASSWORD", "") # app password de 16 chars
```

## Enviar email

```python
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def enviar_email(
    para: str,
    assunto: str,
    corpo: str,
    corpo_html: str | None = None,
    cc: str | None = None,
) -> str:
    msg = MIMEMultipart("alternative" if corpo_html else "mixed")
    msg["Subject"] = assunto
    msg["From"] = EMAIL_USER
    msg["To"] = para
    if cc:
        msg["Cc"] = cc

    msg.attach(MIMEText(corpo, "plain", "utf-8"))
    if corpo_html:
        msg.attach(MIMEText(corpo_html, "html", "utf-8"))

    destinatarios = [para] + ([cc] if cc else [])
    with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as smtp:
        smtp.starttls()
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.sendmail(EMAIL_USER, destinatarios, msg.as_string())
    return f"Email enviado para {para}"

resultado = enviar_email(
    para="destinatario@example.com",
    assunto="Relatório Diário",
    corpo="Segue o relatório...",
)
FINAL_VAR("resultado")
```

## Ler emails (IMAP)

```python
import imaplib
import email as email_lib
from email.header import decode_header

IMAP_HOST = "imap.gmail.com"

def ler_emails(pasta: str = "INBOX", quantidade: int = 5) -> list[dict]:
    with imaplib.IMAP4_SSL(IMAP_HOST) as mail:
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select(pasta)
        
        _, msgs = mail.search(None, "ALL")
        ids = msgs[0].split()[-quantidade:][::-1]
        
        emails = []
        for uid in ids:
            _, data = mail.fetch(uid, "(RFC822)")
            msg = email_lib.message_from_bytes(data[0][1])
            
            # Decodificar assunto
            subject_raw, enc = decode_header(msg["Subject"])[0]
            subject = subject_raw.decode(enc or "utf-8") if isinstance(subject_raw, bytes) else subject_raw
            
            # Corpo texto simples
            corpo = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        corpo = part.get_payload(decode=True).decode("utf-8", errors="replace")
                        break
            else:
                corpo = msg.get_payload(decode=True).decode("utf-8", errors="replace")
            
            emails.append({
                "de": msg["From"],
                "assunto": subject,
                "data": msg["Date"],
                "corpo": corpo[:500],  # primeiros 500 chars
            })
        return emails

emails = ler_emails(quantidade=5)
for e in emails:
    print(f"De: {e['de']}\nAssunto: {e['assunto']}\n{e['corpo'][:200]}\n---")
```

## Pesquisar emails por assunto

```python
def buscar_emails(termo: str, pasta: str = "INBOX") -> list[dict]:
    with imaplib.IMAP4_SSL(IMAP_HOST) as mail:
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select(pasta)
        _, ids = mail.search(None, f'SUBJECT "{termo}"')
        # ... mesmo loop de ler_emails acima
        return []  # retorna lista filtrada

encontrados = buscar_emails("Confirmação de reserva")
```

## Variáveis de ambiente

| Variável | Exemplo | Descrição |
|---|---|---|
| `EMAIL_USER` | `seu@gmail.com` | Endereço de origem |
| `EMAIL_APP_PASSWORD` | `abcd efgh ijkl mnop` | App password 16 chars (Gmail) |
| `EMAIL_HOST` | `smtp.gmail.com` | Host SMTP |

Para Outlook: `smtp.office365.com`, porta 587.
