# -*- coding: utf-8 -*-
"""
每日情报简报 v1.0
=================

结构（A01 五段）：
    触发（任务计划 08:00）→ 取数（多源并发）→ 处理（关键词打分/去重/分类）
    → 输出（Markdown + 微信推送）→ 通知（失败告警）

配置：
    sources.json   数据源清单（改这里就能加/删源）
    keywords.json  关键词权重（改这里就能调"什么值得看"）
    .env           PUSHPLUS_TOKEN

运行：
    .venv\\Scripts\\python.exe report.py
"""

from __future__ import annotations

import datetime as dt
import html
import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

OUT_DIR = BASE / "output"
LOG_DIR = BASE / "logs"
OUT_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

TIMEOUT = 20
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

# 两套会话：优先走系统代理（有 VPN 时），失败再直连（无 VPN 时）
SESSION = requests.Session()          # 跟随系统代理（Phantom 等）
DIRECT = requests.Session()
DIRECT.trust_env = False              # 直连，不受残留代理影响

CATEGORY_LABEL = {
    "job": "招聘信号",
    "hk": "香港",
    "policy": "政策",
    "intl": "专业视角",
    "global": "全球视野",
    "finance": "全球财经",
    "research": "研究前沿",
    "tech": "技术与变现",
}
CATEGORY_ORDER = ["job", "hk", "policy", "intl", "global", "finance", "research", "tech"]
LIMITS = {"job": 2, "hk": 3, "policy": 2, "intl": 3, "global": 2, "finance": 2, "research": 1, "tech": 4}
MAX_PER_SOURCE = 2

# 相关性门槛：标题里必须出现至少一个"硬词"，否则再新也不要
MUST_HAVE = [
    "工务", "基建", "建造", "工程", "招标", "铁路", "地铁", "水务", "公屋",
    "BIM", "数字孪生", "智能建造", "装配式", "数据中心", "算力", "发展局",
    "规划", "标准", "规范", "施工", "造价", "合同", "招聘", "校招", "实习",
    "construction", "infrastructure", "digital twin", "data center",
    "megaproject", "procurement", "tender", "engineering",
]
TECH_HINTS = [
    "AI", "LLM", "agent", "大模型", "人工智能", "automation", "自动化",
    "open source", "开源", "data", "数据", "BIM", "数字孪生", "机器人",
]
RESEARCH_HINTS = [
    "construction", "infrastructure", "digital twin", "BIM", "LLM",
    "machine learning", "project management", "asset management",
]
INTL_HINTS = MUST_HAVE + [
    "project", "water", "rail", "road", "bridge", "tunnel", "housing",
    "energy", "contract", "bid", "build", "engineer", "dam", "port",
    "airport", "plant", "grid", "sewer", "flood",
]
POLICY_HINTS = [
    "规划", "投资", "项目", "批复", "标准", "规范", "工程", "建设",
    "基础设施", "产业", "能源", "交通", "招标", "采购",]
FINANCE_HINTS = [
    "market", "stock", "bond", "yield", "inflation", "rate", "fed",
    "economy", "economic", "trade", "tariff", "bank", "currency",
    "dollar", "oil", "gold", "earnings", "ipo", "investor", "fund",
    "debt", "gdp", "recession", "equity", "valuation",
]

# 每天轮换一个"求职雷达"入口（按你关注的城市：深圳 / 济南 / 全国平台）
WATCHLIST = [
    ("国聘（央企/国企）", "https://www.iguopin.com/"),
    ("应届生求职网", "https://www.yingjiesheng.com/"),
    ("深圳市地铁集团", "https://www.szmc.net/"),
    ("深圳市建筑工务署", "https://szwb.sz.gov.cn/"),
    ("深圳国资国企", "https://gzw.sz.gov.cn/"),
    ("山东高速集团", "https://www.sdhsg.com/"),
    ("山东省国资委", "http://gzw.shandong.gov.cn/"),
    ("高校人才网", "https://www.gaoxiaojob.com/"),
]


def setup_logging() -> None:
    log_file = LOG_DIR / f"run-{dt.date.today():%Y%m%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def load_json(name: str):
    return json.loads((BASE / name).read_text(encoding="utf-8"))


def fetch_text(url: str) -> str | None:
    for attempt in range(1, 3):
        for sess, mode in ((SESSION, "代理"), (DIRECT, "直连")):
            try:
                resp = sess.get(url, headers=UA, timeout=TIMEOUT)
                resp.raise_for_status()
                resp.encoding = resp.apparent_encoding or resp.encoding
                return resp.text
            except Exception as exc:  # noqa: BLE001
                logging.warning("请求失败（第 %s 次/%s）%s：%s", attempt, mode, url, exc)
    logging.error("放弃：%s", url)
    return None


def parse_published(entry) -> dt.datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return dt.datetime(*t[:6])
            except Exception:  # noqa: BLE001
                return None
    return None


def parse_source(src: dict) -> list[dict]:
    text = fetch_text(src["url"])
    if not text:
        return []
    items: list[dict] = []

    if src["type"] in ("rss", "atom"):
        feed = feedparser.parse(text)
        for entry in feed.entries[: src.get("limit", 30)]:
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            summary = entry.get("summary") or entry.get("description") or ""
            desc = BeautifulSoup(summary, "lxml").get_text(" ", strip=True)[:200]
            items.append(
                {
                    "title": title,
                    "desc": desc,
                    "url": entry.get("link", ""),
                    "source": src["name"],
                    "category": src["category"],
                    "published": parse_published(entry),
                    "weight": src.get("weight", 1.0),
                    "lang": src.get("lang", "zh"),
                    "gate": src.get("gate", True),
                }
            )

    elif src["type"] == "html":
        soup = BeautifulSoup(text, "lxml")
        for a in soup.select(src["selector"])[: src.get("limit", 15)]:
            title = (a.get("title") or a.get_text(strip=True) or "").strip()
            href = a.get("href", "")
            if not title or not href:
                continue
            items.append(
                {
                    "title": title,
                    "url": urljoin(src.get("base", src["url"]), href),
                    "source": src["name"],
                    "category": src["category"],
                    "published": None,
                    "weight": src.get("weight", 1.0),
                    "lang": src.get("lang", "zh"),
                }
            )
    return items


def keyword_hit(text: str, kw: str) -> bool:
    """英文按词边界匹配（避免 AI 命中 said、BIM 命中 bimodulus），中文用子串。"""
    if re.fullmatch(r"[A-Za-z0-9 .+\-]+", kw):
        pattern = rf"(?<![A-Za-z]){re.escape(kw)}(?![A-Za-z])"
        return re.search(pattern, text, re.I) is not None
    return kw in text


def score_item(item: dict, keywords: dict) -> float:
    text = item["title"] + " " + item.get("desc", "")
    total = item["weight"]

    for cat, kws in keywords.items():
        for kw, w in kws.items():
            if keyword_hit(text, kw):
                total += w * (1.5 if cat == item["category"] else 1.0)

    job_score = sum(
        w for kw, w in keywords["job"].items() if keyword_hit(item["title"], kw)
    )
    if job_score >= 5:
        item["category"] = "job"
        total += job_score

    if item["published"]:
        age = (dt.datetime.now() - item["published"]).days
        total += 3 if age <= 1 else 2 if age <= 3 else 1 if age <= 7 else 0

    item["score"] = round(total, 2)
    return total


def dedupe(items: list[dict]) -> list[dict]:
    """标题归一化去重 + 相似度去重（防同一件事的两种说法）。"""
    import difflib

    seen_keys: set[str] = set()
    seen_norms: list[str] = []
    kept: list[dict] = []
    for item in items:
        key = re.sub(r"\W+", "", item["title"].lower())[:40]
        if key in seen_keys:
            continue
        norm = re.sub(r"\s+", "", item["title"])[:60]
        if any(difflib.SequenceMatcher(None, norm, n).ratio() > 0.82 for n in seen_norms):
            continue
        seen_keys.add(key)
        seen_norms.append(norm)
        kept.append(item)
    return kept


def is_recent(item: dict, days: int = 7) -> bool:
    """没有日期的当作新的；有日期的超过 N 天就丢掉（防止 RSS 里翻出旧文）。"""
    pub = item.get("published")
    if not pub:
        return True
    return (dt.datetime.now() - pub).days <= days


def qualifies(item: dict) -> bool:
    title = item["title"]
    text = title + " " + item.get("desc", "")
    cat = item["category"]
    if cat == "finance":
        return is_recent(item, 7) and any(keyword_hit(text, k) for k in FINANCE_HINTS)
    if cat == "global":
        return is_recent(item, 7)
    if item.get("gate", True) is False:
        return is_recent(item, 7)
    if cat == "job":
        return True
    if cat == "global":
        return True
    if cat == "finance":
        return True
    if cat == "tech":
        return any(keyword_hit(title, k) for k in TECH_HINTS)
    if cat == "research":
        return any(keyword_hit(title, k) for k in RESEARCH_HINTS)
    if cat == "intl":
        return any(keyword_hit(title, k) for k in INTL_HINTS)
    if cat == "policy":
        return any(keyword_hit(text, k) for k in POLICY_HINTS)
    return any(keyword_hit(title, k) for k in MUST_HAVE)


def select_top(items: list[dict]) -> dict[str, list[dict]]:
    by_cat: dict[str, list[dict]] = {c: [] for c in CATEGORY_ORDER}
    counts: dict[tuple[str, str], int] = {}
    for item in sorted(items, key=lambda x: x["score"], reverse=True):
        cat = item["category"]
        if cat not in by_cat or len(by_cat[cat]) >= LIMITS[cat]:
            continue
        # 国际/全球/财经/技术：每个来源最多 1 条，保证看到不同媒体
        cap = 1 if cat in ("intl", "global", "finance", "tech") else MAX_PER_SOURCE
        key = (cat, item["source"])
        if counts.get(key, 0) >= cap:
            continue
        by_cat[cat].append(item)
        counts[key] = counts.get(key, 0) + 1

    # 招聘栏目经常为空：用"今日盯一个"兜底，保证每天有一个动作
    if not by_cat["job"]:
        now = dt.datetime.now()
        name, url = WATCHLIST[now.timetuple().tm_yday % len(WATCHLIST)]
        by_cat["job"].append(
            {
                "title": f"今日盯：{name}",
                "url": url,
                "source": "求职雷达",
                "category": "job",
                "published": now,
                "lang": "zh",
                "score": 0,
            }
        )
    return by_cat


def fmt_date(item: dict) -> str:
    return f"{item['published']:%m-%d}" if item["published"] else ""


def pick_lesson(lessons: list[dict], now: dt.datetime, start: dt.date | None = None):
    """按开始日期排课；没有开始日期就按一年中的第几天轮换。"""
    if not lessons:
        return None, 0
    if start:
        idx = max((now.date() - start).days, 0)
        return lessons[idx % len(lessons)], idx + 1
    idx = now.timetuple().tm_yday
    return lessons[idx % len(lessons)], idx


def render_markdown(top: dict[str, list[dict]], stats: dict, now: dt.datetime, lesson: dict | None, day_no: int = 0) -> str:
    lines = [
        f"# 每日情报 · {now:%Y-%m-%d}",
        "",
        f"> 自动生成于 {now:%Y-%m-%d %H:%M} ｜ 抓取 {stats['total']} 条，入选 {stats['picked']} 条",
        "",
    ]
    for cat in CATEGORY_ORDER:
        items = top[cat]
        if not items:
            continue
        lines += [f"## {CATEGORY_LABEL[cat]}", ""]
        for item in items:
            d = fmt_date(item)
            tag = f" `{item['source']}`" if item["lang"] == "en" else f" · {item['source']}"
            lines.append(f"- [{item['title']}]({item['url']}){tag}{(' · ' + d) if d else ''}")
            if item.get("summary"):
                extra = " · ".join(x for x in (item.get("use", ""), item.get("worth", "")) if x)
                lines.append(f"  > {item['summary']}" + (f"（{extra}）" if extra else ""))
        lines.append("")

    if lesson:
        head = f"每日一学 · 第 {day_no} 天 · {lesson['title']}" if day_no else f"每日一学 · {lesson['title']}"
        lines += [f"## {head}", "", f"> {lesson.get('tag', '')}", "", lesson["body"], ""]

    name, url = WATCHLIST[now.timetuple().tm_yday % len(WATCHLIST)]
    lines += ["## 今日盯一个", "", f"[{name}]({url}) — 点开看一眼有没有新公告。", ""]
    lines += ["---", "", "改数据源：`sources.json`；改筛选权重：`keywords.json`；知识点：`lessons.json`。", ""]
    return "\n".join(lines)


def render_push_html(top: dict[str, list[dict]], stats: dict, now: dt.datetime, lesson: dict | None, day_no: int = 0) -> str:
    parts = [f"<h3>{now:%m-%d} 情报简报</h3>"]
    for cat in CATEGORY_ORDER:
        items = top[cat]
        if not items:
            continue
        parts.append(f"<p><b>{CATEGORY_LABEL[cat]}</b><br>")
        for item in items:
            parts.append(
                f'· <a href="{item["url"]}">{html.escape(item["title"])}</a><br>'
            )
            if item.get("summary"):
                extra = " · ".join(x for x in (item.get("use", ""), item.get("worth", "")) if x)
                parts.append(
                    f"<span style='color:#888;font-size:13px'>{html.escape(item['summary'])}"
                    + (f"（{html.escape(extra)}）" if extra else "")
                    + "</span><br>"
                )
        parts.append("</p>")

    if lesson:
        body = lesson["body"]
        if len(body) > 320:
            body = body[:320] + "…"
        parts.append(
            f"<p><b>每日一学 · 第 {day_no} 天 · {html.escape(lesson['title'])}</b><br>"
            f"<span style='color:#888'>{html.escape(lesson.get('tag', ''))}</span><br>"
            f"{html.escape(body)}</p>"
        )

    name, url = WATCHLIST[now.timetuple().tm_yday % len(WATCHLIST)]
    parts.append(f'<p><b>今日盯一个</b><br><a href="{url}">{name}</a></p>')
    parts.append(f"<p style='color:#888'>抓取 {stats['total']} 条 / 入选 {stats['picked']} 条</p>")
    return "".join(parts)


def push_wechat(title: str, content: str) -> bool:
    token = os.getenv("PUSHPLUS_TOKEN", "").strip()
    if not token:
        logging.info("未配置 PUSHPLUS_TOKEN，跳过推送")
        return False
    payload = {
        "token": token,
        "title": title[:100],
        "content": content,
        "template": "html",
        "channel": "wechat",
    }
    for attempt in range(1, 3):
        try:
            data = SESSION.post(
                "https://www.pushplus.plus/send", json=payload, timeout=TIMEOUT
            ).json()
            if data.get("code") == 200:
                logging.info("微信推送成功")
                return True
            logging.warning("推送失败（第 %s 次）：%s", attempt, data)
        except Exception as exc:  # noqa: BLE001
            logging.warning("推送异常（第 %s 次）：%s", attempt, exc)
    return False


def main() -> int:
    setup_logging()
    now = dt.datetime.now()
    sources = load_json("sources.json")
    keywords = load_json("keywords.json")
    logging.info("开始抓取 %s 个数据源", len(sources))

    all_items: list[dict] = []
    ok_sources = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(parse_source, s): s for s in sources}
        for fut in as_completed(futures):
            src = futures[fut]
            try:
                items = fut.result()
            except Exception as exc:  # noqa: BLE001
                logging.error("解析失败 %s：%s", src["name"], exc)
                items = []
            if items:
                ok_sources += 1
            logging.info("%-28s %s 条", src["name"], len(items))
            all_items.extend(items)

    for item in all_items:
        score_item(item, keywords)

    relevant = [x for x in all_items if qualifies(x)]
    unique = dedupe(relevant)
    top = select_top(unique)
    picked = sum(len(v) for v in top.values())
    stats = {"total": len(all_items), "picked": picked}
    logging.info("抓取 %s 条，去重后 %s 条，入选 %s 条", len(all_items), len(unique), picked)

    # 导出结构化数据，供 enrich.py 生成摘要
    export = []
    for cat in CATEGORY_ORDER:
        for it in top[cat]:
            export.append(
                {
                    "category": cat,
                    "source": it["source"],
                    "title": it["title"],
                    "url": it["url"],
                    "desc": it.get("desc", ""),
                    "date": f"{it['published']:%Y-%m-%d}" if it.get("published") else "",
                }
            )
    (OUT_DIR / f"items-{now:%Y-%m-%d}.json").write_text(
        json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # AI 摘要（DeepSeek）；失败不影响主流程
    if "--no-enrich" not in sys.argv and export:
        try:
            from enrich import summarize

            rows = summarize(export)
            if rows:
                idx = 0
                for cat in CATEGORY_ORDER:
                    for it in top[cat]:
                        row = rows[idx] if idx < len(rows) else {}
                        if row:
                            it["summary"] = row.get("s", "")
                            it["use"] = row.get("u", "")
                            it["worth"] = row.get("w", "")
                        idx += 1
                logging.info("已附加 AI 摘要 %d 条", len(rows))
        except Exception as exc:  # noqa: BLE001
            logging.warning("摘要模块异常：%s", exc)

    lesson_data = load_json("lessons.json")
    if isinstance(lesson_data, dict):
        lessons = lesson_data.get("lessons", [])
        start_s = lesson_data.get("start")
        start = dt.date.fromisoformat(start_s) if start_s else None
    else:
        lessons, start = lesson_data, None
    lesson, day_no = pick_lesson(lessons, now, start)

    md = render_markdown(top, stats, now, lesson, day_no)
    out_file = OUT_DIR / f"情报-{now:%Y-%m-%d}.md"
    out_file.write_text(md, encoding="utf-8")
    logging.info("已写入：%s（每日一学：%s）", out_file, lesson["title"] if lesson else "无")

    title = f"{now:%m-%d} Rowan-Zhao早报｜{picked} 条精选"
    pushed = False if "--no-push" in sys.argv else push_wechat(title, render_push_html(top, stats, now, lesson, day_no))

    print("\n" + "=" * 56)
    print(f"完成：{out_file}")
    print(f"源成功 {ok_sources}/{len(sources)} ｜ 抓取 {len(all_items)} 条 ｜ 入选 {picked} 条")
    for cat in CATEGORY_ORDER:
        if top[cat]:
            print(f"  {CATEGORY_LABEL[cat]}：{len(top[cat])} 条")
    print(f"微信推送：{'成功' if pushed else '未发送'}")
    print("=" * 56 + "\n")

    return 0 if ok_sources > 0 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
