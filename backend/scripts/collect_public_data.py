"""公开数据采集脚本（爬虫）—— 独立工具，不接入业务流。

为什么独立：它是「数据获取能力」的练手件，跟实验室预约业务没有耦合。
硬塞进业务链路会让项目叙事变散，所以放在 scripts/ 下，作为可单独运行的采集器。

默认目标：https://quotes.toscrape.com/ —— 专门给爬虫练手的公开站点，
robots.txt 明确允许抓取，不会给别人添麻烦。换目标站只需改 `parse_page()`。

覆盖的工程点（面试就讲这些）：
    1. robots.txt 合规检查      2. UA + timeout
    3. 失败重试 + 指数退避      4. 请求限速（随机间隔，别打挂人家）
    5. CSS 选择器解析           6. 业务键去重
    7. 断点续爬（已采 key 落盘） 8. 落 CSV（也可改落库）

用法：
    cd backend
    ./.venv/Scripts/python.exe scripts/collect_public_data.py                 # 采全站
    ./.venv/Scripts/python.exe scripts/collect_public_data.py --limit 2       # 只采 2 页
    ./.venv/Scripts/python.exe scripts/collect_public_data.py --proxy http://127.0.0.1:7897

依赖：requests / beautifulsoup4 / lxml（见 scripts/requirements-scripts.txt，不属于主程序依赖）
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import time
from pathlib import Path
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://quotes.toscrape.com"
START_URL = f"{BASE_URL}/"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "spider"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MAX_RETRY = 3          # 单个 URL 最多重试 3 次
TIMEOUT = 10           # 秒
MIN_DELAY, MAX_DELAY = 0.6, 1.2   # 每次请求之间随机停顿


def build_session(proxy: str | None) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def check_robots(session: requests.Session, base_url: str) -> bool:
    """先看人家允不允许。不允许就退出 —— 这是底线，不是可选项。"""
    try:
        resp = session.get(f"{base_url}/robots.txt", timeout=TIMEOUT)
        if resp.status_code != 200:
            print("[robots] 站点没有 robots.txt，按允许处理")
            return True
        parser = RobotFileParser()
        parser.parse(resp.text.splitlines())
        allowed = parser.can_fetch(UA, base_url + "/")
        print(f"[robots] can_fetch={allowed}")
        return allowed
    except Exception as exc:  # noqa: BLE001
        print(f"[robots] 检查失败（{exc}），保守起见继续但只采公开页")
        return True


def fetch(session: requests.Session, url: str) -> str | None:
    """带重试 + 指数退避的 GET。失败返回 None，不抛异常打断整批采集。"""
    for attempt in range(1, MAX_RETRY + 1):
        try:
            resp = session.get(url, timeout=TIMEOUT)
            resp.raise_for_status()
            # 有些站不带 charset，requests 会猜错；显式按 utf-8 解更稳
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except requests.RequestException as exc:
            wait = 2 ** (attempt - 1)          # 1s → 2s → 4s
            print(f"  [retry {attempt}/{MAX_RETRY}] {url} 失败：{exc}，{wait}s 后重试")
            time.sleep(wait)
    print(f"  [give up] {url}")
    return None


def parse_page(html: str) -> tuple[list[dict], str | None]:
    """解析一页，返回 (数据列表, 下一页链接)。

    ★ 换目标站只改这个函数：换选择器、换字段。
      别用正则解析 HTML —— 结构一变就碎。
    """
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict] = []

    for box in soup.select("div.quote"):
        text = box.select_one("span.text")
        author = box.select_one("span small.author")
        tags = [t.get_text(strip=True) for t in box.select("div.tags a.tag")]
        if not text or not author:
            continue
        rows.append(
            {
                "quote": text.get_text(strip=True).strip("“”\""),
                "author": author.get_text(strip=True),
                "tags": "|".join(tags),
            }
        )

    next_a = soup.select_one("li.next a")
    next_url = next_a.get("href") if next_a else None
    return rows, next_url


def load_seen(path: Path) -> set[str]:
    if path.exists():
        return set(json.loads(path.read_text(encoding="utf-8")))
    return set()


def save_seen(path: Path, seen: set[str]) -> None:
    path.write_text(json.dumps(sorted(seen), ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="公开数据采集（练手件）")
    parser.add_argument("--limit", type=int, default=0, help="最多采多少页，0 表示不限")
    parser.add_argument("--proxy", default=None, help="如 http://127.0.0.1:7897")
    parser.add_argument("--out", default=str(OUT_DIR / "quotes.csv"))
    args = parser.parse_args()

    session = build_session(args.proxy)
    if not check_robots(session, BASE_URL):
        print("robots.txt 不允许，退出")
        return

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path = out_path.with_suffix(".seen.json")
    seen = load_seen(seen_path)

    url: str | None = START_URL
    page = 0
    all_rows: list[dict] = []
    skipped = 0

    while url:
        page += 1
        if args.limit and page > args.limit:
            break

        print(f"[page {page}] {url}")
        html = fetch(session, url)
        if html is None:
            break

        rows, next_href = parse_page(html)
        for row in rows:
            # 业务唯一键：作者 + 正文前 60 字（换站时改成 id 或 url 更稳）
            key = f"{row['author']}::{row['quote'][:60]}"
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            all_rows.append(row)

        print(f"  本页 {len(rows)} 条，累计新增 {len(all_rows)} 条，跳过重复 {skipped} 条")

        url = urljoin(BASE_URL + "/", next_href) if next_href else None
        if url:
            # 限速：随机间隔，既是礼貌也是最基本的反反爬
            time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    if not all_rows:
        print("没有新增数据（可能都采过了）")
        return

    # 追加写：配合 seen 集合实现断点续爬
    is_new = not out_path.exists()
    with out_path.open("a", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=["quote", "author", "tags"])
        if is_new:
            writer.writeheader()
        writer.writerows(all_rows)

    save_seen(seen_path, seen)
    print(f"\n完成：新增 {len(all_rows)} 条 → {out_path}")
    print(f"已采集合 {len(seen)} 条，下次运行会自动跳过")


if __name__ == "__main__":
    main()
