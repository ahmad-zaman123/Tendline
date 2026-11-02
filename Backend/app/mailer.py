import smtplib
from email.message import EmailMessage
from html import escape

SMTP_TIMEOUT_SECONDS = 10
# Mail clients drop web fonts and stylesheets, so everything is inline with system fonts.
FONT = "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


def render_html(heading, text, action_label, action_url, note_lead, note):
    """Lay out one email in Tendline's colours, using tables so it holds up in every mail client."""
    heading, text = escape(heading), escape(text)
    label, url = escape(action_label), escape(action_url, quote=True)
    note_lead, note = escape(note_lead), escape(note)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>{heading}</title>
</head>
<body style="margin:0; padding:0; background-color:#ebe8df;">
<div style="display:none; max-height:0; overflow:hidden; opacity:0; font-size:1px; line-height:1px; color:#ebe8df;">{label}. {note_lead}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#ebe8df;">
<tr><td align="center" style="padding:40px 16px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%; max-width:480px;">
    <tr><td style="padding:0 4px 22px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
        <td width="28" height="28" align="center" style="width:28px; height:28px; background-color:#0b6657; border-radius:7px; font-family:{FONT}; font-size:15px; font-weight:700; line-height:28px; color:#ffffff;">T</td>
        <td style="padding-left:9px; font-family:{FONT}; font-size:17px; font-weight:700; color:#1b1a15;">Tendline</td>
      </tr></table>
    </td></tr>
    <tr><td style="background-color:#faf8f1; border:1px solid #c9c5b7; border-radius:12px; padding:34px 32px;">
      <h1 style="margin:0 0 12px; font-family:{FONT}; font-size:22px; font-weight:700; line-height:1.3; color:#1b1a15;">{heading}</h1>
      <p style="margin:0 0 26px; font-family:{FONT}; font-size:15px; line-height:1.6; color:#625f54;">{text}</p>
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
        <td style="background-color:#0b6657; border-radius:8px;">
          <a href="{url}" target="_blank" style="display:inline-block; padding:12px 22px; font-family:{FONT}; font-size:15px; font-weight:600; color:#ffffff; text-decoration:none; border-radius:8px;">{label}</a>
        </td>
      </tr></table>
      <p style="margin:26px 0 0; padding-top:16px; border-top:1px solid #dedbcf; font-family:{FONT}; font-size:13px; line-height:1.6; color:#625f54;"><strong style="color:#a85800;">{note_lead}</strong> {note}</p>
    </td></tr>
    <tr><td align="center" style="padding:20px 4px 0; font-family:{FONT}; font-size:12px; line-height:1.6; color:#625f54;">
      Tendline: AI support with a person one step away<br>This is an automated message, so replies aren&rsquo;t read.
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>
"""


def smtp_sender(host, port, user, password, sender):
    """Return a function that sends one email with a single call-to-action link."""

    def send(to, subject, heading, text, action_label, action_url, note_lead, note):
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = to
        message.set_content(f"{heading}\n\n{text}\n\n{action_label}: {action_url}\n\n{note_lead} {note}\n")
        message.add_alternative(render_html(heading, text, action_label, action_url, note_lead, note), subtype="html")
        with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(message)

    return send
