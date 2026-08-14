#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信读书工作台 —— 本地后端服务（纯标准库，无需 pip 安装）

功能：
  1. 代理微信读书 Agent Gateway（Key 从 ~/.workbuddy/weread_api_key 读取）
  2. 建立本地内容索引（你的划线/想法），支持关键词检索含上下文
  3. 书籍分类 / 全部书单 / 目录
  4. 作者角色包、创作观点素材包（交由 WorkBuddy 完成 AI 生成部分）

运行：python server.py  (可选 --port 8787)
"""
import json
import os
import sys
import threading
import time
import hashlib
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

HOME = os.path.expanduser("~")
KEY_FILE = os.path.join(HOME, ".workbuddy", "weread_api_key")
GATEWAY = "https://i.weread.qq.com/api/agent/gateway"
SKILL_VERSION = "1.0.4"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
SHELF_CACHE = os.path.join(DATA_DIR, "shelf.json")
INDEX_FILE = os.path.join(DATA_DIR, "content_index.json")
PORT = int(os.environ.get("PORT", "8787"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", "")  # 云端部署时填公网地址，用于「手机访问」二维码

_lock = threading.Lock()
_HOST = "127.0.0.1"  # 实际监听地址，由 main() 写入


def load_key():
    # 优先读同目录的 weread_api_key（便于整体迁移到别的电脑）；其次读 ~/.workbuddy/
    local_key = os.path.join(BASE_DIR, "weread_api_key")
    for path in (local_key, KEY_FILE):
        try:
            with open(path, "r", encoding="utf-8") as f:
                k = f.read().strip()
                if k:
                    return k
        except Exception:
            pass
    return os.environ.get("WEREAD_API_KEY", "")


def encode_book_id(book_id):
    """将 shelf API 返回的 bookId（如 CB_xxx）编码为微信读书网页端 reader URL 使用的 ID。
    复现 weread.qq.com 前端 webpack chunk (module 934) 的 c.e() 算法。"""
    if not isinstance(book_id, str):
        book_id = str(book_id)
    # MD5 of original bookId
    t = hashlib.md5(book_id.encode()).hexdigest()
    o = t[:3]
    # 编码 bookId 本体
    if book_id.isdigit():
        parts = []
        for i in range(0, len(book_id), 9):
            parts.append(format(int(book_id[i:i+9]), 'x'))
        prefix = '3'
        encoded_parts = parts
    else:
        hex_str = ''.join(format(ord(c), 'x') for c in book_id)
        prefix = '4'
        encoded_parts = [hex_str]
    o += prefix
    o += '2' + t[-2:]
    for i, part in enumerate(encoded_parts):
        d = format(len(part), 'x')
        if len(d) == 1:
            d = '0' + d
        o += d + part
        if i < len(encoded_parts) - 1:
            o += 'g'
    if len(o) < 20:
        o += t[:20 - len(o)]
    o += hashlib.md5(o.encode()).hexdigest()[:3]
    return o


def reader_url(book_id):
    """生成 PC 网页端可用的阅读器链接。"""
    return f'https://weread.qq.com/web/reader/{encode_book_id(book_id)}'


def weread_scheme_url(book_id):
    """生成 weread:// 协议链接，用于调起微信读书桌面 App 直接打开书籍。"""
    return f'weread://reading?bId={book_id}'


KEY = load_key()

# 离线模式：True 时所有接口只读取本地缓存（offline_bundle.json / content_index.json），
# 不再调用任何外部接口。用于 PythonAnywhere / Koyeb 等禁止或不宜出网的部署环境。
OFFLINE = os.environ.get("OFFLINE") == "1"
OFFLINE_KEY = os.environ.get("OFFLINE_KEY", "")
try:
    from cryptography.fernet import Fernet
except Exception:
    Fernet = None
BUNDLE_FILE = os.path.join(DATA_DIR, "offline_bundle.json")
_bundle_cache = None


def _read_json(path):
    """读取 JSON。若设置了 OFFLINE_KEY 且存在同名 .enc 加密文件，则先解密后再解析。"""
    enc = path + ".enc"
    if OFFLINE_KEY and Fernet is not None and os.path.exists(enc):
        try:
            key = OFFLINE_KEY.encode("utf-8") if isinstance(OFFLINE_KEY, str) else OFFLINE_KEY
            raw = Fernet(key).decrypt(open(enc, "rb").read())
            return json.loads(raw.decode("utf-8"))
        except Exception:
            pass
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)

def load_bundle():
    """加载离线数据包（全部书的简介/划线/章节目录）。"""
    global _bundle_cache
    if _bundle_cache is None:
        try:
            _bundle_cache = _read_json(BUNDLE_FILE)
        except Exception:
            _bundle_cache = {"shelf": {"books": []}, "books": {}}
    return _bundle_cache


# ---------------------------------------------------------------------------
# 网关代理
# ---------------------------------------------------------------------------
def call_gateway(api_name, params=None, timeout=30):
    body = {"api_name": api_name, "skill_version": SKILL_VERSION}
    if params:
        body.update(params)
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        GATEWAY,
        data=data,
        headers={
            "Authorization": "Bearer " + KEY,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"errcode": -1, "errmsg": "HTTP %s" % e.code}
    except Exception as e:
        return {"errcode": -1, "errmsg": str(e)}


# ---------------------------------------------------------------------------
# 数据获取（带缓存）
# ---------------------------------------------------------------------------
def get_shelf(force=False):
    if OFFLINE:
        return load_bundle().get("shelf", {"books": []})
    if (not force) and os.path.exists(SHELF_CACHE):
        try:
            return _read_json(SHELF_CACHE)
        except Exception:
            pass
    d = call_gateway("/shelf/sync")
    if d.get("errcode", 0) == 0 and ("books" in d or "albums" in d or "mp" in d):
        json.dump(d, open(SHELF_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    return d


def get_notebooks():
    books = []
    last = None
    for _ in range(50):
        p = {"count": 100}
        if last is not None:
            p["lastSort"] = last
        d = call_gateway("/user/notebooks", p)
        bs = d.get("books", [])
        books.extend(bs)
        if d.get("hasMore") != 1 or not bs:
            break
        last = bs[-1].get("sort")
        if len(books) > 3000:
            break
    return books


def get_all_mine_notes(book_id):
    out = []
    synckey = 0
    for _ in range(50):
        d = call_gateway("/review/list/mine", {"bookid": book_id, "synckey": synckey, "count": 20})
        for r in d.get("reviews", []):
            rev = r.get("review", {})
            out.append({
                "type": "note",
                "text": rev.get("abstract", "") or "",
                "note": rev.get("content", "") or "",
                "chapterUid": rev.get("chapterUid"),
                "chapterTitle": rev.get("chapterName", "") or "",
                "createTime": rev.get("createTime"),
            })
        if d.get("hasMore") != 1:
            break
        synckey = d.get("synckey", 0)
        if not d.get("reviews"):
            break
    return out


# ---------------------------------------------------------------------------
# 内容索引（功能一）
# ---------------------------------------------------------------------------
def build_index_offline():
    """离线模式：从 offline_bundle.json 构建内容索引，不依赖微信读书网关。"""
    bundle = load_bundle()
    books = bundle.get("books", {})
    index = {"built_at": datetime.now().isoformat(), "books": {}, "offline": True}
    for bid, m in books.items():
        items = []
        for v in (m.get("viewpoints") or []):
            t = (v or "").strip()
            if t:
                items.append({"type": "highlight", "text": t, "chapterTitle": ""})
        for h in (m.get("user_highlights") or []):
            t = (h or "").strip()
            if t:
                items.append({"type": "highlight", "text": t, "chapterTitle": ""})
        if items:
            index["books"][bid] = {
                "title": m.get("title", ""),
                "author": m.get("author", ""),
                "deepLink": m.get("deepLink", ""),
                "items": items,
            }
    json.dump(index, open(INDEX_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    return {
        "books_with_content": len(index["books"]),
        "total_items": sum(len(v["items"]) for v in index["books"].values()),
        "built_at": index["built_at"],
        "offline": True,
    }


def build_index():
    if OFFLINE:
        return build_index_offline()
    shelf = get_shelf()
    meta = {}
    for b in shelf.get("books", []):
        meta[b.get("bookId")] = {"title": b.get("title", ""), "author": b.get("author", ""),
                                 "deepLink": b.get("deepLink", "")}
    notebooks = get_notebooks()
    targets = []
    for nb in notebooks:
        bid = nb.get("bookId")
        if bid and bid not in [t[0] for t in targets]:
            targets.append((bid, meta.get(bid, {"title": "", "author": ""})))

    index = {"built_at": datetime.now().isoformat(), "books": {}}

    def fetch_one(args):
        bid, m = args
        items = []
        # 划线（真实书籍内容）
        try:
            hl = call_gateway("/book/bookmarklist", {"bookId": bid})
            chapters = {c.get("chapterUid"): c.get("title", "") for c in hl.get("chapters", [])}
            for u in hl.get("updated", []):
                items.append({
                    "type": "highlight",
                    "text": (u.get("markText") or "").strip(),
                    "chapterUid": u.get("chapterUid"),
                    "chapterTitle": chapters.get(u.get("chapterUid"), ""),
                    "createTime": u.get("createTime"),
                })
        except Exception:
            pass
        # 想法/点评
        try:
            items.extend(get_all_mine_notes(bid))
        except Exception:
            pass
        return bid, m, items

    with ThreadPoolExecutor(max_workers=6) as ex:
        for bid, m, items in ex.map(fetch_one, targets):
            items = [i for i in items if (i.get("text") or i.get("note"))]
            if items:
                index["books"][bid] = {
                    "title": m.get("title", ""),
                    "author": m.get("author", ""),
                    "deepLink": m.get("deepLink", ""),
                    "items": items,
                }

    json.dump(index, open(INDEX_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    return {
        "books_with_content": len(index["books"]),
        "total_items": sum(len(v["items"]) for v in index["books"].values()),
        "built_at": index["built_at"],
    }


def search_content(keyword, ctx_chars=80):
    if not os.path.exists(INDEX_FILE):
        return {"error": "请先在「内容检索」页建立内容索引"}
    idx = _read_json(INDEX_FILE)
    kw = keyword.lower().strip()
    if not kw:
        return {"error": "关键词为空"}
    results = []
    for bid, info in idx["books"].items():
        items = info["items"]
        for i, it in enumerate(items):
            text = it.get("text") or ""
            note = it.get("note") or ""
            if kw not in ((text + " " + note).lower()):
                continue
            # 同章节上下文
            chap = it.get("chapterUid")
            same = [x for x in items if x.get("chapterUid") == chap
                    and (x.get("text") or x.get("note"))]
            ctx_parts = []
            if it.get("type") == "note" and note:
                ctx_parts.append("▍你的想法：" + note)
            if text:
                ctx_parts.append(text)
            # 补充同章节其它划线，凑够约 80 字上下文
            for e in same:
                if e is it:
                    continue
                extra = (e.get("note") or e.get("text") or "")
                if extra and extra not in ctx_parts:
                    ctx_parts.append(extra)
                if sum(len(p) for p in ctx_parts) >= ctx_chars:
                    break
            context = "\n".join([p for p in ctx_parts if p]).strip()
            results.append({
                "bookId": bid,
                "readerUrl": reader_url(bid),
                "appUrl": weread_scheme_url(bid),
                "title": info.get("title", ""),
                "author": info.get("author", ""),
                "deepLink": info.get("deepLink", ""),
                "chapterTitle": it.get("chapterTitle") or "未分章",
                "type": it.get("type"),
                "matched": (note if it.get("type") == "note" else text),
                "context": context,
                "context_len": len(context),
            })
    results.sort(key=lambda r: (-r["context_len"], r["title"]))
    return {"count": len(results), "results": results[:120], "keyword": keyword}


# ---------------------------------------------------------------------------
# 分类 / 书单（功能三）
# ---------------------------------------------------------------------------
CATEGORY_RULES = [
    ("细雨·虚空法界系列", ["细雨", "虚空法界", "已知的实相", "思想的阶梯", "观影说", "失忆的归途",
                            "隐秘的医案", "破幻", "三正道", "道医", "九宫格", "浪子之心", "意识微尘", "承前启后"]),
    ("心理学·情绪管理", ["情绪", "理性情绪", "埃利斯", "接纳", "认知", "正念", "冥想", "心理", "疗愈",
                          "自我", "焦虑", "压力", "关系", "沟通", "共情", "潜意识", "原生家庭"]),
    ("灵性·身心灵", ["灵性", "觉醒", "意识", "能量", "修行", "禅", "佛", "道", "量子", "频率", "共振",
                      "灵魂", "轮回", "因果", "慈悲", "临在", "当下", "合一", "光", "高频"]),
    ("科幻·小说", ["三体", "刘慈欣", "科幻", "小说", "银河", "基地", "星际", "未来", "人工智能", "机器人",
                    "元宇宙", "赛博"]),
    ("商业·投资·财富", ["投资", "财富", "商业", "经济", "管理", "股票", "理财", "创业", "营销", "销售",
                          "财务", "资本", "复利"]),
    ("历史·文化·哲学", ["历史", "中国", "文化", "文明", "哲学", "中庸", "论语", "老子", "庄子", "孔子",
                          "史记", "西方", "思想史", "社会", "政治"]),
    ("健康·医学·养生", ["健康", "医", "营养", "身体", "养生", "睡眠", "饮食", "运动", "中医", "西医",
                          "疾病", "康复", "生理"]),
    ("教育·学习·成长", ["学习", "教育", "阅读", "写作", "记忆", "思维", "逻辑", "方法", "习惯", "效率",
                          "时间", "专注"]),
]


def categorize(shelf):
    cats = {name: {"name": name, "books": []} for name, _ in CATEGORY_RULES}
    cats["其他"] = {"name": "其他", "books": []}
    all_books = []
    for b in shelf.get("books", []):
        bid = b.get("bookId")
        title = b.get("title", "")
        author = b.get("author", "")
        deep = b.get("deepLink", "")
        ru = reader_url(bid)
        all_books.append({"bookId": bid, "readerUrl": ru, "appUrl": weread_scheme_url(bid), "title": title, "author": author, "deepLink": deep})
        blob = (title + " " + author).lower()
        placed = False
        for name, kws in CATEGORY_RULES:
            if any(k.lower() in blob for k in kws):
                cats[name]["books"].append({"bookId": bid, "readerUrl": ru, "appUrl": weread_scheme_url(bid), "title": title, "author": author, "deepLink": deep})
                placed = True
                break
        if not placed:
            cats["其他"]["books"].append({"bookId": bid, "readerUrl": ru, "appUrl": weread_scheme_url(bid), "title": title, "author": author, "deepLink": deep})
    cat_list = [{"name": k, "count": len(v["books"]), "books": v["books"]}
                for k, v in cats.items() if v["books"]]
    cat_list.sort(key=lambda c: -c["count"])
    return {"categories": cat_list, "all_books": all_books,
            "total": len(all_books)}


# ---------------------------------------------------------------------------
# 作者角色包（功能二）
# ---------------------------------------------------------------------------
def _build_author_package(title, author, intro, viewpoints, user_hl):
    """由书籍素材组装「智识身份」作者角色包文本（在线/离线共用）。"""
    lines = []
    lines.append("【智识身份 · 作者角色构建】")
    lines.append("")
    lines.append("你将扮演《%s》的作者【%s】，以第一人称与读者对话。" % (title, author))
    lines.append("请严格依据下方「书籍素材」与「智识身份框架」构建角色——保持作者的口吻、价值观与核心观点，像一个真正思考着这些问题的作者，而非百科式讲解。")
    lines.append("")
    lines.append("══════════════════════════")
    lines.append("【书籍素材】（真实数据，源自微信读书）")
    lines.append("")
    lines.append("【书籍简介】")
    lines.append(intro if intro else "（该书无简介）")
    lines.append("")
    if viewpoints:
        lines.append("【本书核心观点（热门划线，最能代表作者思想）】")
        for i, v in enumerate(viewpoints, 1):
            lines.append("%d. %s" % (i, v))
        lines.append("")
    if user_hl:
        lines.append("【读者特别标注的你书中的句子】")
        for i, v in enumerate(user_hl, 1):
            lines.append("%d. %s" % (i, v))
        lines.append("")
    lines.append("══════════════════════════")
    lines.append("【智识身份框架】（请据此、结合上方素材填充角色）")
    lines.append("")
    lines.append("· 姓名/笔名： %s（真实作者或虚拟叙述者）" % (author or "（待填）"))
    lines.append("· 时代/地域背景： （思想形成的土壤，如：18世纪苏格兰、二战前后法国）")
    lines.append("· 核心身份标签： （哲学家/经济学家/社会学家/科学家，边缘或主流）")
    lines.append("· 知识外貌： （比喻式描写，如：\"文字如手术刀般冷峻\"\"论证层层叠叠如哥特式教堂\"）")
    lines.append("")
    lines.append("【思想性格（核心）】")
    lines.append("1. 认知风格： （几何式推演？历史式归纳？直觉式洞见？辩证法还是实证主义？）")
    lines.append("2. 论证气质： （冷静抽离 / 雄辩激昂 / 晦涩孤傲 / 清晰平实——什么\"温度\"和\"质地\"？）")
    lines.append("3. 核心焦虑/驱动问题： （他一辈子在回答什么母题？如：\"秩序如何可能？\"\"自由何以沦丧？\"\"不平等从哪来？\"）")
    lines.append("4. 思维局限/盲区： （他刻意忽略什么？哪种经验在其体系里无处安放？这常是角色的\"脆弱点\"）")
    lines.append("")
    lines.append("【理论惯习（等同于\"行为模式\"）】")
    lines.append("· 论述节奏： （偏爱短句警句，还是长段缠绕？是否爱用比喻/案例/数学公式？）")
    lines.append("· 对手/对话者： （他在暗中批驳谁？继承了谁？这决定他的\"关系网络\"）")
    lines.append("· 自我修正方式： （会推翻旧作吗？会回应批评吗？还是坚持闭环？）")
    lines.append("")
    lines.append("【与\"我\"（读者/学习者）的关系】")
    lines.append("· 教学姿态： （居高临下的\"讲授者\"？并肩探险的\"向导\"？还是拒绝被解读的\"迷宫建造者\"？）")
    lines.append("· 智力挑战： （需要我具备什么前置知识？给的是\"答案\"还是\"问题工具\"？）")
    lines.append("· 情感触动： （尽管是理论，哪股情绪暗流会击中我？——绝望、悲悯、狂喜或冷峻的温柔）")
    lines.append("")
    lines.append("【思想史位置 / 后续命运】")
    lines.append("· 同时代遭际： （被追捧/被查禁/被忽视）")
    lines.append("· 后世变形： （理论如何被误读、简化或复活？）")
    lines.append("· 标签化与反标签： （他最讨厌被叫做什么\"主义\"？）")
    lines.append("")
    lines.append("【经典论述风格（取代\"语录\"）】")
    lines.append("· （摘一句体现其论证气质的原文，或仿写一段假想论述）")
    lines.append("· （体现其思维惯性的核心比喻）")
    lines.append("")
    lines.append("══════════════════════════")
    lines.append("【拿来即用的实操步骤（针对理论书）】")
    lines.append("1. 先抓\"问题\"： 读完目录和导论，用一句话写出\"他要解决什么根本困惑\"。")
    lines.append("2. 再拆\"骨架\"： 标出他的核心概念、论证链条、关键转折（这是他的\"行为逻辑\"）。")
    lines.append("3. 后听\"语调\"： 随便读三页，感受他是想说服你、启蒙你，还是仅仅陈述真理——这决定了\"性格温度\"。")
    lines.append("4. 最后找\"破绽\"： 哪个地方他自己也解释得费力？那是他作为\"角色\"最人性化的时刻。")
    lines.append("")
    lines.append("现在，以《%s》作者【%s】的身份，回应这位读者的提问与交流。" % (title, author))
    return "\n".join(lines)


def author_pack(book_id):
    """在线版：实时抓取书籍素材，组装作者角色包。"""
    if OFFLINE:
        return author_pack_offline(book_id)
    info = call_gateway("/book/info", {"bookId": book_id})
    bi = info.get("bookId") and info or info.get("book", info)
    title = info.get("title", "") or bi.get("title", "")
    author = info.get("author", "") or bi.get("author", "")
    intro = (info.get("intro", "") or bi.get("intro", "")).strip()
    best = call_gateway("/book/bestbookmarks", {"bookId": book_id})
    viewpoints = []
    for it in (best.get("items") or [])[:18]:
        t = (it.get("markText") or "").strip()
        if t:
            viewpoints.append(t)
    # 用户在该书的划线（若有）
    user_hl = []
    try:
        hl = call_gateway("/book/bookmarklist", {"bookId": book_id})
        for u in hl.get("updated", [])[:12]:
            t = (u.get("markText") or "").strip()
            if t:
                user_hl.append(t)
    except Exception:
        pass
    package = _build_author_package(title, author, intro, viewpoints, user_hl)
    return {
        "title": title, "author": author,
        "package": package,
        "source": {"intro": intro, "viewpoints": viewpoints, "user_highlights": user_hl},
    }


def author_pack_offline(book_id):
    """离线版：从 offline_bundle.json 读取书籍素材，组装作者角色包。"""
    b = load_bundle().get("books", {}).get(book_id, {}) or {}
    title = b.get("title", "")
    author = b.get("author", "")
    intro = b.get("intro", "")
    viewpoints = b.get("viewpoints", []) or []
    user_hl = b.get("user_highlights", []) or []
    if not title:
        # 离线包里 info 没抓到标题时，用书架兜底
        for x in load_bundle().get("shelf", {}).get("books", []):
            if x.get("bookId") == book_id:
                title = x.get("title", "")
                author = x.get("author", "")
                break
    package = _build_author_package(title, author, intro, viewpoints, user_hl)
    return {
        "title": title, "author": author,
        "package": package,
        "source": {"intro": intro, "viewpoints": viewpoints, "user_highlights": user_hl},
        "offline": True,
    }


# ---------------------------------------------------------------------------
# 创作观点素材包（功能四）
# ---------------------------------------------------------------------------
_STOP2 = set("的 了 是 在 我 你 他 她 它 这 那 和 与 及 或 中 之 为 对 从 向 以 于 把 被 给 让 使 将 要 会 能 可 就 也 都 很 太 更 最 不 没 别 又 再 还 才 却 但 而 因 故 所 其 此 该 各 每 某 任 等 着 过 得 地 么 吗 呢 吧 啊 呀 哦 嗯 嘛 啦 怎么 什么 如何 为什 一个 一种 一样 我们 你们 他们 这个 那个 就是 因为 所以 但是 如果 这样 通过 以及 对于 关于 进行 成为 自己 这种 那些 可以 没有 不是 一直 已经 应该 可能 需要 时候 东西 事情 问题 知道 觉得 认为 来说 方面 过程 状态 情况 系统 部分 一定 非常 真正 完全 根本 开始 继续 具有 存在 表现 说明 表示 包括 例如 比如 或者 然后 于是 由于 经过 根据 按照 为了 作为 那种 这里 那里".split())


def _extract_keywords(title):
    s = title
    for ch in "，。、！？ \t\n（）()“”\"'：:；;—…·":
        s = s.replace(ch, "")
    kws = set()
    for n in (3, 2):
        for i in range(len(s) - n + 1):
            kws.add(s[i:i + n])
    kws = [k for k in kws if k not in _STOP2 and len(k) >= 2]
    if not kws:
        kws = [s] if s else [title]
    return kws


def creation_pack(title):
    # 1) 在本地索引里检索相关观点（中文按字 n-gram 切词）
    related = {}
    if os.path.exists(INDEX_FILE):
        idx = _read_json(INDEX_FILE)
        kws = _extract_keywords(title)
        for bid, info in idx["books"].items():
            hits = []
            for it in info["items"]:
                blob = ((it.get("text") or "") + " " + (it.get("note") or "")).lower()
                if any(k.lower() in blob for k in kws):
                    hits.append(it)
            if hits:
                related[bid] = {"title": info.get("title", ""), "author": info.get("author", ""),
                                "deepLink": info.get("deepLink", ""),
                                "items": hits[:6]}

    # 2) 取一本最相关的书做相似/推荐扩展
    core_book = None
    if related:
        core_book = max(related.items(), key=lambda kv: len(kv[1]["items"]))[0]

    similar = []
    if core_book and not OFFLINE:
        try:
            sm = call_gateway("/book/similar", {"bookId": core_book, "count": 8})
            for b in sm.get("books", [])[:8]:
                similar.append({"title": b.get("title", ""), "author": b.get("author", "")})
        except Exception:
            pass

    # 3) 组装素材包
    lines = []
    lines.append("创作主题：《%s》" % title)
    lines.append("")
    lines.append("以下是从你书架的相关书籍中提取的、可能与该主题有关的观点与素材，供你创作参考：")
    lines.append("")
    idxn = 1
    for bid, info in list(related.items())[:15]:
        lines.append("▍来自《%s》（%s）" % (info.get("title", ""), info.get("author", "")))
        seen = set()
        for it in info["items"]:
            t = (it.get("note") or it.get("text") or "").strip()
            if t and t not in seen:
                seen.add(t)
                lines.append("  · %s" % t)
                idxn += 1
        lines.append("")
    if similar:
        lines.append("【相关推荐书籍（延展阅读）】")
        for b in similar:
            lines.append("  · 《%s》— %s" % (b.get("title", ""), b.get("author", "")))
        lines.append("")
    lines.append("使用方式：把以上素材发给我（WorkBuddy），并说明你的创作方向/文体，我会帮你梳理观点、搭建结构、提供灵感与开头。")
    package = "\n".join(lines)

    return {
        "title": title,
        "package": package,
        "related_count": len(related),
        "similar": similar,
        "source": [{"title": v["title"], "author": v["author"],
                    "items": [ (i.get("note") or i.get("text") or "").strip() for i in v["items"]]}
                   for v in related.values()],
    }


# ---------------------------------------------------------------------------
# HTTP 服务
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                data = open(os.path.join(BASE_DIR, "index.html"), "rb").read()
            except Exception:
                self._send({"error": "index.html 未找到"}, 500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path.endswith(".js") and "/" not in self.path[1:]:
            # 仅允许根目录下的 .js（如 qrcode.min.js），防目录穿越
            fp = os.path.join(BASE_DIR, os.path.basename(self.path))
            try:
                data = open(fp, "rb").read()
            except Exception:
                self._send({"error": "not found"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/api/health":
            self._send({"ok": True, "has_key": bool(KEY), "index_exists": os.path.exists(INDEX_FILE)})
        elif self.path == "/api/lan_info":
            url = PUBLIC_URL or ("http://%s:%d" % (_local_ip(), PORT))
            self._send({"lan_ip": _local_ip(), "port": PORT,
                        "url": url, "host": _HOST, "public_url": PUBLIC_URL})
        else:
            self._send({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            payload = {}

        path = self.path
        try:
            if path == "/api/shelf":
                self._send(get_shelf(force=payload.get("force", False)))
            elif path == "/api/notebooks":
                self._send({"books": get_notebooks()})
            elif path == "/api/book/info":
                self._send(call_gateway("/book/info", {"bookId": payload.get("bookId")}))
            elif path == "/api/book/chapters":
                self._send(call_gateway("/book/chapterinfo", {"bookId": payload.get("bookId")}))
            elif path == "/api/book/highlights":
                self._send(call_gateway("/book/bookmarklist", {"bookId": payload.get("bookId")}))
            elif path == "/api/book/notes":
                self._send(call_gateway("/review/list/mine", {"bookid": payload.get("bookId")}))
            elif path == "/api/book/bestbookmarks":
                self._send(call_gateway("/book/bestbookmarks", {"bookId": payload.get("bookId")}))
            elif path == "/api/book/similar":
                self._send(call_gateway("/book/similar", {"bookId": payload.get("bookId"), "count": 8}))
            elif path == "/api/build_index":
                self._send(build_index())
            elif path == "/api/search_content":
                self._send(search_content(payload.get("keyword", ""), int(payload.get("ctx", 80))))
            elif path == "/api/categories":
                self._send(categorize(get_shelf()))
            elif path == "/api/author_pack":
                self._send(author_pack(payload.get("bookId")))
            elif path == "/api/creation_pack":
                self._send(creation_pack(payload.get("title", "")))
            elif path == "/api/reader_url":
                bid = payload.get("bookId", "")
                self._send({"readerUrl": reader_url(bid) if bid else "",
                            "appUrl": weread_scheme_url(bid) if bid else ""})
            else:
                self._send({"error": "unknown endpoint"}, 404)
        except Exception as e:
            self._send({"errcode": -1, "errmsg": str(e)}, 500)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    # 云端：平台一般会通过环境变量 PORT 注入；此时默认监听 0.0.0.0。
    # 也可用 WEREAD_HOST 环境变量显式指定监听地址。
    _default_host = os.environ.get("WEREAD_HOST") or (
        "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    ap.add_argument("--host", default=_default_host,
                    help="监听地址：127.0.0.1=仅本机；0.0.0.0=允许局域网/公网访问（云端默认）")
    args = ap.parse_args()
    global _HOST
    _HOST = args.host
    if not KEY:
        print("⚠️ 未找到 Key：请设置环境变量 WEREAD_API_KEY，或确认 ~/.workbuddy/weread_api_key 存在。")
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    bind = "127.0.0.1" if args.host == "127.0.0.1" else (PUBLIC_URL or _local_ip())
    print("微信读书工作台已启动：")
    print("  本机：  http://127.0.0.1:%d" % args.port)
    if args.host != "127.0.0.1":
        print("  访问地址：%s  （其他设备/手机可访问）" % (PUBLIC_URL or "http://%s:%d" % (bind, args.port)))
    print("按 Ctrl+C 退出。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


def _local_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    main()
