"""抓正文 + 用 DeepSeek 生成"看完就知道讲了什么"的摘要。"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / "output"
API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

SYSTEM = (
    "你是土木工程与职业规划导师，读者是土木本科、即将读港大数字基础设施硕士的学生。\n"
    "我会给你文章标题和正文节选。请为每篇写：\n"
    "1) s：2-3 句话（60-110 字）说清这篇讲了什么——谁做了什么、关键数字或结论、这意味着什么。"
    "**禁止复述标题**，禁止写'本文介绍了'这类空话，必须给具体信息。\n"
    "2) u：对读者的用处，20-35 字，要具体，格式如'求职：面试可讲 CMAR 与 DBB 的区别'"
    "或'专业：数据中心液冷对土建荷载的影响'。\n"
    "3) w：值得点开写'看'，扫一眼即可写'扫'。\n"
    '只输出 JSON：{"items":[{"i":1,"s":"...","u":"...","w":"看"}]}，不要解释。'
)


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = True
    return s


def fetch_article(url: str, limit: int = 1200) -> str:
    """抓正文前若干字；失败返回空字符串。"""
    if not url:
        return ""
    for trust in (True, False):
        sess = requests.Session()
        sess.trust_env = trust
        try:
            resp = sess.get(url, headers=UA, timeout=12)
            if resp.status_code != 200:
                continue
            resp.encoding = resp.apparent_encoding or resp.encoding
            soup = BeautifulSoup(resp.text, "lxml")
            for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
                tag.decompose()
            ps = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
            text = " ".join(x for x in ps if len(x) > 40)
            if len(text) > 200:
                return text[:limit]
        except Exception:  # noqa: BLE001
            continue
    return ""


def summarize(items: list[dict], timeout: int = 90) -> list[dict]:
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        logging.info("未配置 DEEPSEEK_API_KEY，跳过摘要")
        return []

    blocks = []
    for i, it in enumerate(items, 1):
        body = fetch_article(it.get("url", ""))
        if not body:
            body = (it.get("desc") or "")[:600]
        blocks.append(
            f"{i}. 标题：{it.get('title','')}\n来源：{it.get('source','')}\n正文节选：{body[:900]}"
        )

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"共 {len(items)} 篇：\n\n" + "\n\n".join(blocks)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.4,
        "max_tokens": 4000,
    }

    sess = requests.Session()
    sess.trust_env = False
    for attempt in range(1, 3):
        try:
            resp = sess.post(
                API_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
            out = [{} for _ in items]
            for row in data.get("items", []):
                idx = int(row.get("i", 0)) - 1
                if 0 <= idx < len(out):
                    out[idx] = {
                        "s": str(row.get("s", ""))[:220],
                        "u": str(row.get("u", ""))[:60],
                        "w": str(row.get("w", ""))[:2],
                    }
            logging.info("摘要生成成功：%d 条", len(data.get("items", [])))
            return out
        except Exception as exc:  # noqa: BLE001
            logging.warning("摘要失败（第 %s 次）：%s", attempt, exc)
    return []


def main() -> None:
    import datetime as dt

    path = OUT_DIR / f"items-{dt.date.today():%Y-%m-%d}.json"
    items = json.loads(path.read_text(encoding="utf-8"))
    result = summarize(items)
    (OUT_DIR / f"summaries-{dt.date.today():%Y-%m-%d}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
