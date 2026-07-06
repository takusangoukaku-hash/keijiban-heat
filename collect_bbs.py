#!/usr/bin/env python3
"""
Yahoo!掲示板ランキング 自動収集スクリプト(GitHub Actions用)
docs/data/history.json にスナップショットを蓄積し、PWA(docs/index.html)が読み込む。

出力形式(PWAのスナップショット形式と同一):
[{"at": ISO8601, "source": str,
  "entries": [{"rank":int, "code":str, "name":str,
               "market":"prime|standard|growth|etf|unknown",
               "mcap":null, "chg":float|None}, ...]}, ...]

注意: Yahoo!ファイナンスの規約上、自動取得は原則禁止。1日1回・数リクエストの
      個人利用に留め、リポジトリは非公開推奨。
"""
import json, re, sys, time, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
BASE = "https://finance.yahoo.co.jp/stocks/ranking/bbs"
OUT = Path(__file__).parent / "docs" / "data" / "history.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0",
           "Accept-Language": "ja,en;q=0.8"}
MKT = {"東証PRM": "prime", "東証STD": "standard", "東証GRT": "growth",
       "東証ETF": "etf", "ETF": "etf"}
KEEP = 60  # 保持スナップショット数(約2ヶ月分)


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def walk(node, found):
    if isinstance(node, list):
        if (len(node) >= 10 and all(isinstance(x, dict) for x in node[:10])
                and any("rank" in x or "rankingNumber" in x for x in node[:3])):
            found.append(node)
        for x in node:
            walk(x, found)
    elif isinstance(node, dict):
        for v in node.values():
            walk(v, found)


def norm_market(v):
    if not v:
        return "unknown"
    s = str(v)
    for k, m in MKT.items():
        if k in s:
            return m
    if re.search(r"プライム|prime", s, re.I): return "prime"
    if re.search(r"スタンダード|standard", s, re.I): return "standard"
    if re.search(r"グロース|growth", s, re.I): return "growth"
    return "unknown"


def parse_next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    cands = []
    walk(data, cands)
    for cand in cands:
        entries = []
        for it in cand:
            code = None
            for k in ("code", "stockCode", "securityCode", "ticker"):
                v = it.get(k)
                if isinstance(v, str) and re.fullmatch(r"[0-9][0-9A-Z]{3}(\.[A-Z])?", v):
                    code = v.split(".")[0]
                    break
            if not code:
                continue
            rank = it.get("rank") or it.get("rankingNumber")
            if not rank:
                continue
            name = it.get("name") or it.get("stockName") or code
            market = "unknown"
            for k, v in it.items():
                if re.search(r"market|exchange|section", k, re.I):
                    nm = norm_market(v)
                    if nm != "unknown":
                        market = nm
                        break
            chg = None
            for k, v in it.items():
                if re.search(r"chang.*(rate|ratio|percent)|riseAndFallRate", k, re.I):
                    try:
                        chg = round(float(str(v).replace("%", "").replace("+", "")), 2)
                        break
                    except (ValueError, TypeError):
                        pass
            entries.append({"rank": int(rank), "code": code, "name": str(name),
                            "market": market, "mcap": None, "chg": chg})
        if len(entries) >= 10:
            return entries
    return []


def parse_table(html):
    """フォールバック: HTMLの行から 順位/コード/名称/市場区分 を抽出"""
    entries = []
    rows = re.split(r"<tr[\s>]", html)
    for row in rows:
        rk = re.search(r">(\d{1,3})<", row)
        link = re.search(r'/quote/([0-9][0-9A-Z]{3})(?:\.T)?"[^>]*>([^<]+)<', row)
        if not (rk and link):
            continue
        mkt = "unknown"
        mm = re.search(r"東証(PRM|STD|GRT|ETF)", row)
        if mm:
            mkt = MKT.get("東証" + mm.group(1), "unknown")
        entries.append({"rank": int(rk.group(1)), "code": link.group(1),
                        "name": link.group(2).strip(), "market": mkt,
                        "mcap": None, "chg": None})
    # 重複除去
    seen, uniq = set(), []
    for e in sorted(entries, key=lambda x: x["rank"]):
        if e["code"] not in seen:
            seen.add(e["code"])
            uniq.append(e)
    return uniq


def main():
    entries = []
    for page in (1, 2):
        html = fetch(f"{BASE}?market=all&term=daily&page={page}")
        got = parse_next_data(html) or parse_table(html)
        entries.extend(got)
        if len(got) < 50:
            break
        time.sleep(2)
    seen, uniq = set(), []
    for e in sorted(entries, key=lambda x: x["rank"]):
        if e["code"] not in seen:
            seen.add(e["code"])
            uniq.append(e)
    if len(uniq) < 20:
        print(f"取得失敗: {len(uniq)}件のみ。ページ構造変更の可能性", file=sys.stderr)
        sys.exit(1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    history = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []
    now = datetime.now(JST)
    # 同日重複を防ぐ(同じ日に再実行したら置き換え)
    today = now.date().isoformat()
    history = [s for s in history if s["at"][:10] != today]
    history.append({"at": now.isoformat(timespec="seconds"),
                    "source": f"GitHub Actions自動収集({len(uniq)}件)",
                    "entries": uniq})
    history = history[-KEEP:]
    OUT.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    unk = sum(1 for e in uniq if e["market"] == "unknown")
    print(f"OK: {len(uniq)}件保存 (市場区分不明{unk}件) -> {OUT}")


if __name__ == "__main__":
    main()
