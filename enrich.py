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
    "1) s：3-4 句话（120-180 字），按这个结构写：\n"
    "   ① 发生了什么：谁、做了什么、在哪、关键数字（金额/规模/工期/数量）；\n"
    "   ② 怎么做的或为什么：方法、模式、背景、争议（如 CMAR、TBM、政策变化）；\n"
    "   ③ 意味着什么：对行业/政策/技术的一句话判断。\n"
    "   **禁止复述标题，禁止'本文介绍了'这类空话；文中有数字必须保留。**\n"
    "2) u：对读者的用处，30-50 字，具体到能怎么用，格式如"
    "'求职：面试可讲 CMAR 与 DBB 的区别'、'论文：工期压缩失效可作案例'、"
    "'专业：深隧 TBM 的埋深与竖井设计'。\n"
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

    # 1) 先抓正文，按原顺序存好
    bodies: list[str] = []
    for it in items:
        body = fetch_article(it.get("url", ""))
        if not body:
            body = (it.get("desc") or "")[:600]
        bodies.append(body)

    sess = requests.Session()
    sess.trust_env = False
    out: list[dict] = [{} for _ in items]

    # 2) 分批，每批内部从 1 重新编号（避免全局编号错位）
    batch_size = 5
    for start in range(0, len(items), batch_size):
        end = min(start + batch_size, len(items))
        chunk_lines = []
        for j in range(start, end):
            local = j - start + 1
            chunk_lines.append(
                f"{local}. 标题：{items[j].get('title','')}\n"
                f"来源：{items[j].get('source','')}\n"
                f"正文节选：{bodies[j][:1400]}"
            )
        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"共 {end - start} 篇：\n\n" + "\n\n".join(chunk_lines)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.4,
            "max_tokens": 4000,
        }
        for attempt in range(1, 3):
            try:
                resp = sess.post(
                    API_URL,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=timeout,
                )
                resp.raise_for_status()
                data = json.loads(resp.json()["choices"][0]["message"]["content"])
                for row in data.get("items", []):
                    local = int(row.get("i", 0)) - 1
                    if 0 <= local < (end - start):
                        out[start + local] = {
                            "s": str(row.get("s", ""))[:420],
                            "u": str(row.get("u", ""))[:90],
                            "w": str(row.get("w", ""))[:2],
                        }
                break
            except Exception as exc:  # noqa: BLE001
                logging.warning("摘要失败（第 %s 批/第 %s 次）：%s", start // batch_size + 1, attempt, exc)

    filled = sum(1 for x in out if x)
    logging.info("摘要生成成功：%d/%d 条", filled, len(items))
    return out


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
