import smtplib
from email.message import EmailMessage
from html import escape

SMTP_TIMEOUT_SECONDS = 10


def smtp_sender(host, port, user, password, sender):
    """Return a function that sends one email with a single call-to-action link."""

    def send(to, subject, text, action_label, action_url):
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = to
        message.set_content(f"{text}\n\n{action_label}: {action_url}\n")
        message.add_alternative(
            f"<p>{escape(text)}</p>"
            f'<p><a href="{escape(action_url, quote=True)}">{escape(action_label)}</a></p>',
            subtype="html",
        )
        with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(message)

    return send
