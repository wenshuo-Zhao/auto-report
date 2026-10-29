"""用 DeepSeek 给当天精选条目生成摘要、用处与阅读建议。"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / "output"
API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"

SYSTEM = (
    "你是土木工程与职业规划导师，读者是土木本科、即将读港大数字基础设施硕士的学生。"
    "请为每条信息写三样东西："
    "1) s：一句话摘要，不超过 45 字，说清发生了什么或提出了什么；"
    "2) u：对读者的用处，从 [求职, 专业, 前沿, 视野] 里选 1-2 个，用·连接；"
    "3) w：值得点开写“看”，扫一眼即可写“扫”。"
    "只输出 JSON：{\"items\":[{\"i\":1,\"s\":\"...\",\"u\":\"专业\",\"w\":\"扫\"}]}，不要解释。"
)


def summarize(items: list[dict], timeout: int = 60) -> list[dict]:
    """返回与 items 等长的摘要列表；失败返回空列表。"""
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        logging.info("未配置 DEEPSEEK_API_KEY，跳过摘要")
        return []

    lines = []
    for i, it in enumerate(items, 1):
        desc = (it.get("desc") or "").replace("\n", " ")[:180]
        lines.append(f"{i}. [{it.get('source','')}] {it.get('title','')}｜{desc}")

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "共 %d 条：\n%s" % (len(items), "\n".join(lines))},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.3,
        "max_tokens": 2000,
    }

    session = requests.Session()
    session.trust_env = False
    for attempt in range(1, 3):
        try:
            resp = session.post(
                API_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
            rows = data.get("items", [])
            out = [{} for _ in items]
            for row in rows:
                idx = int(row.get("i", 0)) - 1
                if 0 <= idx < len(out):
                    out[idx] = {
                        "s": str(row.get("s", ""))[:80],
                        "u": str(row.get("u", ""))[:20],
                        "w": str(row.get("w", ""))[:2],
                    }
            logging.info("摘要生成成功：%d 条", len(rows))
            return out
        except Exception as exc:  # noqa: BLE001
            logging.warning("摘要失败（第 %s 次）：%s", attempt, exc)
    return []


def main() -> None:
    import datetime as dt

    path = OUT_DIR / f"items-{dt.date.today():%Y-%m-%d}.json"
    items = json.loads(path.read_text(encoding="utf-8"))
    result = summarize(items)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
