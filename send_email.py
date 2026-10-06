import smtplib, os, pathlib
from email.mime.text import MIMEText
from email.header import Header

body = pathlib.Path("docs/paper_analysis.md").read_text(encoding="utf-8")

msg = MIMEText(body, "plain", "utf-8")
msg["Subject"] = Header("📚 arXiv 每日论文更新", "utf-8")
msg["From"] = os.environ["MAIL_USERNAME"]
msg["To"] = os.environ["MAIL_TO"]

with smtplib.SMTP_SSL("smtp.qq.com", 465) as s:
    s.login(os.environ["MAIL_USERNAME"], os.environ["MAIL_PASSWORD"])
    s.sendmail(msg["From"], [msg["To"]], msg.as_string())
print("邮件发送成功")

