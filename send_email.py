#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arXiv 每日论文邮件推送（HTML 摘要版）

与 GitHub Action `dawidd6/action-send-mail` 的区别：
  - 只发送「本轮新分析」的论文（读 docs/newly_analyzed_papers.json），邮件小、重点突出
  - 自己渲染 HTML 版式，不依赖 Action 的 Markdown 转换，排版稳定
  - 无新论文时默认不发信，避免每天收到一封 3MB 的全量报告

用法：
  # 正常发送（从环境变量读配置）
  python send_email.py

  # 本地预览：只生成 HTML，不发信
  DRY_RUN=1 python send_email.py

环境变量：
  MAIL_USERNAME   SMTP 账号，如 xxx@qq.com            （必填）
  MAIL_PASSWORD   SMTP 授权码（不是登录密码！）        （必填，DRY_RUN 可省）
  MAIL_TO         收件人，多个用英文逗号分隔            （必填）
  MAIL_FROM       发件人地址，默认同 MAIL_USERNAME
  MAIL_SERVER     SMTP 服务器，默认 smtp.qq.com
  MAIL_PORT       SMTP 端口，默认 465
  MAIL_SECURE     true=SSL(465) / false=STARTTLS(587)，默认 true
  SUBJECT_PREFIX  邮件标题前缀，默认 "arXiv 每日论文"
  REPORT_URL      完整报告链接，默认取 GitHub Pages 地址
  MAX_PER_CATEGORY 每个分类最多展示几篇，默认 15
  SEND_IF_EMPTY   无新论文时是否也发一封，默认 false
  FALLBACK_LATEST 无 newly_analyzed 文件时，按发布日期取最近几篇，默认 10（0=不发）
  DRY_RUN         1=只生成预览文件不发信
  PREVIEW_PATH    预览文件路径，默认 docs/email_preview.html
"""

import html
import json
import os
import smtplib
import ssl
import sys
from datetime import datetime
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

NEWLY_ANALYZED_PATH = os.environ.get("NEWLY_ANALYZED_PATH", "docs/newly_analyzed_papers.json")
ANALYSIS_JSON_PATH = os.environ.get("ANALYSIS_JSON_PATH", "docs/agent-arxiv-daily-analysis.json")

CATEGORY_COLORS = {
    "Agent": "#2563eb",
    "Large Language Models": "#7c3aed",
    "Reinforcement Learning": "#059669",
}
DEFAULT_COLOR = "#475569"


# ---------------------------------------------------------------- 工具函数

def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] 读取失败 {path}: {e}")
        return None


def paper_summary(info):
    """从分析结果里取摘要，兼容多种字段名"""
    text = (
        info.get("one_sentence_summary")
        or (info.get("analysis") or {}).get("summary")
        or (info.get("analysis") or {}).get("research_question")
        or ""
    )
    text = " ".join(str(text).split())
    return text[:280] + ("..." if len(text) > 280 else "")


def paper_authors(info):
    authors = (info.get("metadata") or {}).get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    if not authors and info.get("first_author"):
        authors = [info["first_author"]]
    if not authors:
        return ""
    return authors[0] + (" et al." if len(authors) > 1 else "")


def collect_by_ids(analysis_data, ids):
    """按 arxiv id 收集论文，保持分类结构"""
    wanted = set(ids)
    groups = {}
    for category, papers in analysis_data.items():
        if not isinstance(papers, dict):
            continue
        picked = [info for pid, info in papers.items() if pid in wanted and isinstance(info, dict)]
        if picked:
            groups[category] = sorted(picked, key=lambda p: p.get("publish_date") or "", reverse=True)
    return groups


def fallback_latest(analysis_data, limit):
    """没有 newly_analyzed 文件时，取各分类中发布日期最新的若干篇"""
    groups = {}
    for category, papers in analysis_data.items():
        if not isinstance(papers, dict):
            continue
        items = [info for info in papers.values() if isinstance(info, dict)]
        items.sort(key=lambda p: p.get("publish_date") or "", reverse=True)
        if items:
            groups[category] = items[:limit]
    return groups


# ---------------------------------------------------------------- 渲染

def render_paper(info, color):
    pid = info.get("arxiv_id", "")
    title = html.escape(info.get("title") or f"Paper {pid}")
    date = html.escape(info.get("publish_date") or "")
    authors = html.escape(paper_authors(info))
    summary = html.escape(paper_summary(info))

    urls = info.get("urls") or {}
    if not urls:
        res = (info.get("metadata") or {}).get("resources") or {}
        urls = {
            "github": res.get("github"),
            "huggingface": res.get("huggingface"),
            "project_page": res.get("project_page"),
        }

    links = [
        f'<a href="https://arxiv.org/abs/{pid}" style="color:{color};text-decoration:none;">arXiv 摘要</a>',
        f'<a href="https://arxiv.org/pdf/{pid}" style="color:{color};text-decoration:none;">PDF</a>',
    ]
    for label, key in (("代码", "github"), ("模型", "huggingface"), ("项目主页", "project_page")):
        if urls.get(key):
            links.append(
                f'<a href="{html.escape(str(urls[key]), quote=True)}" '
                f'style="color:{color};text-decoration:none;">{label}</a>'
            )

    meta = " · ".join(x for x in (date, authors) if x)
    return f"""
  <div style="padding:14px 16px;border-left:3px solid {color};background:#f8fafc;border-radius:6px;margin:0 0 12px;">
    <div style="font-size:15px;font-weight:600;line-height:1.45;color:#0f172a;">{title}</div>
    <div style="font-size:12px;color:#64748b;margin:5px 0 8px;">{meta}</div>
    <div style="font-size:13px;line-height:1.7;color:#334155;">{summary}</div>
    <div style="font-size:12px;margin-top:9px;">{' · '.join(links)}</div>
  </div>"""


def build_html(groups, updated_at, report_url, max_per_category):
    total = sum(len(v) for v in groups.values())
    cat_nav = "".join(
        f'<span style="display:inline-block;margin:0 8px 6px 0;padding:3px 10px;border-radius:12px;'
        f'background:#eef2ff;color:{CATEGORY_COLORS.get(c, DEFAULT_COLOR)};font-size:12px;">'
        f'{html.escape(c)} · {len(p)} 篇</span>'
        for c, p in groups.items()
    )

    sections = []
    for category, papers in groups.items():
        color = CATEGORY_COLORS.get(category, DEFAULT_COLOR)
        shown = papers[:max_per_category]
        cards = "".join(render_paper(p, color) for p in shown)
        more = ""
        if len(papers) > len(shown):
            more = (
                f'<div style="font-size:12px;color:#94a3b8;padding:2px 0 10px;">'
                f'另有 {len(papers) - len(shown)} 篇，见完整报告</div>'
            )
        sections.append(f"""
  <div style="margin-top:26px;">
    <div style="font-size:16px;font-weight:700;color:{color};border-bottom:2px solid {color};
                padding-bottom:6px;margin-bottom:14px;">{html.escape(category)}
      <span style="font-size:12px;font-weight:400;color:#94a3b8;">（{len(papers)} 篇）</span>
    </div>
    {cards}{more}
  </div>""")

    report_link = (
        f'<a href="{html.escape(report_url, quote=True)}" style="color:#2563eb;text-decoration:none;">'
        f'查看完整报告与历史论文 →</a>' if report_url else ""
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:24px 12px;background:#f1f5f9;
             font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;">
<div style="max-width:680px;margin:0 auto;background:#ffffff;border-radius:12px;
            padding:26px 26px 20px;box-shadow:0 1px 3px rgba(15,23,42,.08);">

  <div style="font-size:21px;font-weight:700;color:#0f172a;">arXiv 每日论文速递</div>
  <div style="font-size:13px;color:#64748b;margin-top:6px;">
    更新于 {html.escape(updated_at)} · 本轮新增 <b style="color:#2563eb;">{total}</b> 篇
  </div>
  <div style="margin-top:14px;">{cat_nav}</div>

  {''.join(sections) if sections else '<div style="padding:28px 0;text-align:center;color:#94a3b8;">本轮没有新论文</div>'}

  <div style="margin-top:28px;padding-top:14px;border-top:1px solid #e2e8f0;
              font-size:12px;color:#94a3b8;line-height:1.8;">
    {report_link}<br>
    摘要由 LLM 自动生成，仅供参考，请以论文原文为准。<br>
    本邮件由 agent-arxiv-daily 自动发送。
  </div>
</div>
</body></html>"""


# ---------------------------------------------------------------- 发送

def send_mail(subject, html_body):
    username = os.environ.get("MAIL_USERNAME")
    password = os.environ.get("MAIL_PASSWORD")
    to = os.environ.get("MAIL_TO")
    if not (username and password and to):
        print("[ERROR] 缺少 MAIL_USERNAME / MAIL_PASSWORD / MAIL_TO")
        return False

    sender = os.environ.get("MAIL_FROM") or username
    server = os.environ.get("MAIL_SERVER", "smtp.qq.com")
    port = int(os.environ.get("MAIL_PORT", "465"))
    secure = os.environ.get("MAIL_SECURE", "true").lower() in ("1", "true", "yes")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header("arXiv Daily", "utf-8")), sender))
    msg["To"] = to
    msg["Message-ID"] = make_msgid()
    msg.attach(MIMEText("本邮件为 HTML 格式，请在支持 HTML 的客户端中查看。", "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    recipients = [a.strip() for a in to.split(",") if a.strip()]
    try:
        if secure:
            with smtplib.SMTP_SSL(server, port, context=ssl.create_default_context(), timeout=30) as s:
                s.login(username, password)
                s.sendmail(sender, recipients, msg.as_string())
        else:
            with smtplib.SMTP(server, port, timeout=30) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(username, password)
                s.sendmail(sender, recipients, msg.as_string())
        print(f"[OK] 邮件已发送 -> {to}")
        return True
    except Exception as e:
        print(f"[ERROR] 发送失败: {e}")
        return False


# ---------------------------------------------------------------- 主流程

def main():
    dry_run = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
    max_per_category = int(os.environ.get("MAX_PER_CATEGORY", "15"))
    send_if_empty = os.environ.get("SEND_IF_EMPTY", "false").lower() in ("1", "true", "yes")
    fallback_latest_n = int(os.environ.get("FALLBACK_LATEST", "10"))

    analysis_data = load_json(ANALYSIS_JSON_PATH)
    if not analysis_data:
        print(f"[ERROR] 无法读取分析文件 {ANALYSIS_JSON_PATH}")
        return 1

    newly = load_json(NEWLY_ANALYZED_PATH)
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    groups = {}

    if newly and newly.get("paper_ids"):
        ids = newly["paper_ids"]
        updated_at = (newly.get("timestamp") or updated_at)[:16]
        groups = collect_by_ids(analysis_data, ids)
        print(f"[INFO] 本轮新分析论文 {len(ids)} 篇，成功匹配 {sum(len(v) for v in groups.values())} 篇")
    elif fallback_latest_n > 0:
        groups = fallback_latest(analysis_data, fallback_latest_n)
        print(f"[WARN] 未找到 {NEWLY_ANALYZED_PATH}，改用各分类最近 {fallback_latest_n} 篇作为兜底")

    total = sum(len(v) for v in groups.values())
    if total == 0 and not send_if_empty:
        print("[INFO] 没有新论文，跳过发送")
        return 0

    owner = os.environ.get("GITHUB_REPOSITORY_OWNER") or os.environ.get("GITHUB_USER_NAME", "")
    repo = (os.environ.get("GITHUB_REPOSITORY", "/").split("/")[-1]) or "agent-arxiv-daily"
    report_url = os.environ.get("REPORT_URL")
    if not report_url and owner:
        report_url = f"https://{owner}.github.io/{repo}/"

    subject_prefix = os.environ.get("SUBJECT_PREFIX", "arXiv 每日论文")
    subject = f"{subject_prefix} | {total} 篇新论文 ({updated_at})"
    html_body = build_html(groups, updated_at, report_url, max_per_category)

    if dry_run:
        path = os.environ.get("PREVIEW_PATH", "docs/email_preview.html")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_body)
        print(f"[DRY_RUN] 预览已生成: {path}（标题：{subject}）")
        return 0

    return 0 if send_mail(subject, html_body) else 1


if __name__ == "__main__":
    sys.exit(main())
