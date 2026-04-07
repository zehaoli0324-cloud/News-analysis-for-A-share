"""
A股量化分析平台 v1.0
三大功能：
  1. 资金选股     —— 全市场策略筛选（原capital-flow-screener）
  2. 单股资金分析 —— 单只/多只股票资金流向评分
  3. 单股新闻资讯 —— AI驱动的新闻聚合+DeepSeek深度分析

后端：Flask + 异步任务队列
"""

import os, sys, io, time, threading, uuid, pathlib, warnings, queue, json
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from flask import Flask, request, jsonify, send_file, Response

app = Flask(__name__, static_folder=".", template_folder=".")
sys.path.insert(0, str(pathlib.Path(__file__).parent))

import screener_core
from strategy_registry import list_strategies, get_strategy

# ══════════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════════
HEARTBEAT_TIMEOUT = 180
TASK_TTL          = 3600

# ══════════════════════════════════════════════════════════════
# 任务中心（复用capital-flow-screener的成熟架构）
# ══════════════════════════════════════════════════════════════
_tasks: dict     = {}
_tasks_lock      = threading.Lock()
_heavy_queue     = queue.Queue()
_light_semaphore = threading.Semaphore(2)


def _new_task(kind: str) -> str:
    task_id = str(uuid.uuid4())
    now = time.time()
    with _tasks_lock:
        _tasks[task_id] = {
            "kind": kind, "status": "queued",
            "log": [], "result": None, "excel": None,
            "excel_ready": False, "actual_date": "",
            "queue_pos": 0, "created_at": now,
            "started_at": None, "finished_at": None,
            "last_heartbeat": now, "combined": None,
            # 新闻分析专用字段
            "news_report": None,      # AI生成的完整报告文本
            "stream_done": False,
            "keywords_data": None,    # 关键词字典，供前端展示
            "subtasks": {},           # 子任务状态 {name: status}
            "chat_history": [],       # 多轮对话历史
            "prompt_context": None,   # 保存prompt供对话复用
            "meta_snapshot": None,    # 保存meta供对话复用
            "symbol_label": "",       # 股票代码+名称，供PDF文件名用
            "all_news": None,         # 所有采集到的新闻，供下载用
        }
    return task_id


def _tlog(task_id: str, msg: str):
    with _tasks_lock:
        t = _tasks.get(task_id)
        if t:
            t["log"].append(msg)


def _set_subtask(task_id: str, name: str, status: str):
    """更新子任务状态：running / done / warn / error"""
    with _tasks_lock:
        t = _tasks.get(task_id)
        if t is not None:
            t["subtasks"][name] = status


def _update_queue_positions():
    items = list(_heavy_queue.queue)
    for pos, (tid, *_) in enumerate(items, start=1):
        with _tasks_lock:
            if tid in _tasks:
                _tasks[tid]["queue_pos"] = pos


# ══════════════════════════════════════════════════════════════
# stdout 捕获（复用原架构）
# ══════════════════════════════════════════════════════════════

class _StdoutRouter:
    def __init__(self, task_id: str):
        self._task_id = task_id
        self._r = self._w = None
        self._reader_thread = None
        self._orig_fd1 = None

    def __enter__(self):
        self._r, self._w = os.pipe()
        self._orig_fd1 = os.dup(1)
        os.dup2(self._w, 1)
        os.close(self._w); self._w = None
        self._orig_sys_stdout = sys.stdout
        sys.stdout = _PipeWriter(self._r)
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        return self

    def _read_loop(self):
        buf = b""
        try:
            with os.fdopen(self._r, "rb", buffering=0) as f:
                while True:
                    chunk = f.read(256)
                    if not chunk: break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        decoded = line.decode("utf-8", errors="replace").strip()
                        if decoded:
                            _tlog(self._task_id, decoded)
        except Exception as e:
            _tlog(self._task_id, f"[警告] 日志捕获异常: {e}")
        if buf:
            decoded = buf.decode("utf-8", errors="replace").strip()
            if decoded:
                _tlog(self._task_id, decoded)

    def __exit__(self, *_):
        sys.stdout = self._orig_sys_stdout
        os.dup2(self._orig_fd1, 1)
        os.close(self._orig_fd1)
        if self._reader_thread:
            self._reader_thread.join(timeout=3)


class _PipeWriter(io.TextIOBase):
    def __init__(self, _r): pass
    def write(self, s):
        try: os.write(1, s.encode("utf-8", errors="replace"))
        except: pass
        return len(s)
    def flush(self): pass


# ══════════════════════════════════════════════════════════════
# Excel 异步生成
# ══════════════════════════════════════════════════════════════

def _generate_excel_async(task_id: str, combined: list, actual_date: str):
    try:
        buf = io.BytesIO()
        screener_core.save_excel(combined, buf)
        buf.seek(0); data = buf.getvalue()
        if len(data) < 1000:
            raise RuntimeError(f"文件异常，大小仅{len(data)}字节")
        with _tasks_lock:
            if task_id in _tasks:
                _tasks[task_id]["excel"] = data
                _tasks[task_id]["excel_ready"] = True
                _tasks[task_id]["combined"] = None
        _tlog(task_id, f"📁 Excel 已生成（{len(data)//1024} KB），可下载")
    except Exception as e:
        _tlog(task_id, f"❌ Excel 生成失败：{e}")


# ══════════════════════════════════════════════════════════════
# 守护线程
# ══════════════════════════════════════════════════════════════

def _watchdog():
    while True:
        time.sleep(60)
        now = time.time()
        with _tasks_lock:
            task_ids = list(_tasks.keys())
        for tid in task_ids:
            with _tasks_lock:
                t = _tasks.get(tid)
                if t is None: continue
                status = t["status"]; hb = t["last_heartbeat"]
                created = t["created_at"]; combined = t.get("combined")
                excel_ready = t.get("excel_ready", False)
                actual_date = t.get("actual_date", "")
            if now - created > TASK_TTL:
                with _tasks_lock: _tasks.pop(tid, None)
                continue
            if status == "done" and not excel_ready and combined:
                if now - hb > HEARTBEAT_TIMEOUT:
                    threading.Thread(target=_generate_excel_async,
                                     args=(tid, combined, actual_date), daemon=True).start()

threading.Thread(target=_watchdog, daemon=True).start()


def _safe(v):
    if isinstance(v, float) and (np.isnan(v) or np.isinf(v)): return None
    if isinstance(v, np.integer): return int(v)
    if isinstance(v, np.floating): return float(v)
    if isinstance(v, pd.DataFrame): return None
    if isinstance(v, list): return [_safe(i) for i in v]
    return v


# ══════════════════════════════════════════════════════════════
# 队列工作线程
# ══════════════════════════════════════════════════════════════

def _heavy_worker():
    import traceback
    while True:
        try:
            task_id, fn, args = _heavy_queue.get()
            with _tasks_lock:
                if task_id in _tasks:
                    _tasks[task_id]["status"] = "running"
                    _tasks[task_id]["queue_pos"] = 0
                    _tasks[task_id]["started_at"] = time.time()
            _update_queue_positions()
            try:
                fn(task_id, *args)
            except Exception as e:
                _tlog(task_id, f"❌ 队列执行异常：{e}")
                _tlog(task_id, traceback.format_exc())
                with _tasks_lock:
                    if task_id in _tasks:
                        _tasks[task_id]["status"] = "error"
            finally:
                with _tasks_lock:
                    if task_id in _tasks:
                        _tasks[task_id]["finished_at"] = time.time()
                _heavy_queue.task_done()
        except Exception as e:
            print(f"[FATAL] heavy_worker crashed: {e}", file=sys.stderr)
            time.sleep(5)


def _submit_heavy(task_id, fn, args):
    _heavy_queue.put((task_id, fn, args))
    _update_queue_positions()


def _submit_light(task_id, fn, args):
    def _wrapper():
        _light_semaphore.acquire()
        with _tasks_lock:
            if task_id in _tasks:
                _tasks[task_id]["status"] = "running"
                _tasks[task_id]["started_at"] = time.time()
        try:
            fn(task_id, *args)
        finally:
            with _tasks_lock:
                if task_id in _tasks:
                    _tasks[task_id]["finished_at"] = time.time()
            _light_semaphore.release()
    threading.Thread(target=_wrapper, daemon=True).start()


_thread_started = False
if not _thread_started:
    threading.Thread(target=_heavy_worker, daemon=True).start()
    _thread_started = True


# ══════════════════════════════════════════════════════════════
# 功能一：全市场资金选股（重任务）
# ══════════════════════════════════════════════════════════════

def _run_screener(task_id: str, token: str, date_str: str, proxy: str, strategy_id: str):
    with _StdoutRouter(task_id):
        def log(msg): _tlog(task_id, msg)
        try:
            import tushare as ts
            from datetime import datetime
            import concurrent.futures

            proxy_url = proxy.strip() if proxy and proxy.strip() else None
            screener_core._PROXY_URL = proxy_url
            log("🔧 代理：" + (proxy_url or "直连"))
            log("🔑 验证 Tushare Token...")
            ts.set_token(token)
            screener_core._ts.set_token(token)
            with concurrent.futures.ThreadPoolExecutor() as pool:
                fut = pool.submit(ts.pro_api)
                try:
                    pro = fut.result(timeout=10)
                except concurrent.futures.TimeoutError:
                    log("❌ Tushare 连接超时（10秒）")
                    with _tasks_lock: _tasks[task_id]["status"] = "error"
                    return
            screener_core._pro = pro
            log("✅ Token OK")

            target_date = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
            if date_str and date_str.strip():
                for fmt in ("%Y%m%d", "%Y-%m-%d"):
                    try:
                        target_date = datetime.strptime(date_str.strip(), fmt); break
                    except ValueError: pass

            is_today = (target_date.date() == datetime.today().date())
            log(f"📅 模式：{'今日实时' if is_today else '历史回测 ' + target_date.strftime('%Y-%m-%d')}")

            try:
                strategy = get_strategy(strategy_id)
                log(f"📋 策略：【{strategy.META['name']}】")
            except KeyError as e:
                log(f"❌ {e}")
                with _tasks_lock: _tasks[task_id]["status"] = "error"
                return

            log("📊 步骤1：全市场行情快照...")
            snapshot_df, actual_date = screener_core.get_spot_data(target_date)
            hs300_chg = screener_core.get_hs300_change(actual_date)
            log(f"✅ {len(snapshot_df)} 只股票，沪深300: {hs300_chg:+.2f}%")
            log(f"📈 步骤2：执行策略选股...")
            candidates = strategy.run(snapshot_df, hs300_chg, actual_date, log)
            if not candidates:
                log("❌ 策略未筛出候选股")
                with _tasks_lock:
                    _tasks[task_id].update({"status": "done", "result": [], "actual_date": actual_date})
                return

            log(f"🎯 {len(candidates)} 只候选股进入资金流向验证...")
            log("💰 步骤3：拉取资金流向...")
            ff_results = screener_core.fetch_fund_flows(candidates, actual_date)
            log("🧮 综合打分...")
            combined = []
            for rec in candidates:
                sc = screener_core.score_fund_flow(
                    rec["code"], ff_results.get(rec["code"]), rec.get("_hist"), rec.get("circ_cap_yi"))
                combined.append({**rec, **sc})
            combined.sort(key=lambda x: (int(x.get("has_ff", False)), x["total"]), reverse=True)
            result_list = [{k: _safe(v) for k, v in r.items() if not k.startswith("_")} for r in combined]
            with _tasks_lock:
                _tasks[task_id].update({"result": result_list, "status": "done",
                                         "actual_date": actual_date, "combined": combined})
            log(f"🎉 完成！筛出 {len(result_list)} 只，正在生成 Excel...")
            threading.Thread(target=_generate_excel_async,
                             args=(task_id, combined, actual_date), daemon=True).start()
        except Exception as e:
            import traceback
            _tlog(task_id, f"❌ 出错：{e}")
            _tlog(task_id, traceback.format_exc())
            with _tasks_lock: _tasks[task_id]["status"] = "error"


# ══════════════════════════════════════════════════════════════
# 功能二：单股资金分析（轻任务）
# ══════════════════════════════════════════════════════════════

def _run_single_analysis(task_id: str, token: str, codes: list, date_str: str, proxy: str):
    with _StdoutRouter(task_id):
        def log(msg): _tlog(task_id, msg)
        try:
            import tushare as ts, re
            from datetime import datetime, timedelta
            import concurrent.futures

            proxy_url = proxy.strip() if proxy and proxy.strip() else None
            screener_core._PROXY_URL = proxy_url
            ts.set_token(token)
            screener_core._ts.set_token(token)
            with concurrent.futures.ThreadPoolExecutor() as pool:
                fut = pool.submit(ts.pro_api)
                try:
                    pro = fut.result(timeout=10)
                except concurrent.futures.TimeoutError:
                    log("❌ Tushare 连接超时")
                    with _tasks_lock: _tasks[task_id]["status"] = "error"; return
            screener_core._pro = pro; log("✅ Token OK")

            actual_date = datetime.today().strftime("%Y%m%d")
            if date_str and date_str.strip():
                for fmt in ("%Y%m%d", "%Y-%m-%d"):
                    try:
                        actual_date = datetime.strptime(date_str.strip(), fmt).strftime("%Y%m%d"); break
                    except ValueError: pass

            clean_codes = list(dict.fromkeys(
                m.group(1) for c in codes
                for m in [re.search(r'\b(\d{6})\b', c.strip())] if m))
            if not clean_codes:
                log("❌ 未识别到有效股票代码")
                with _tasks_lock: _tasks[task_id].update({"status": "done", "result": []}); return

            log(f"📋 待分析：{len(clean_codes)} 只 → {', '.join(clean_codes)}")
            name_map = {}
            try:
                sb = pro.stock_basic(fields="ts_code,name")
                if sb is not None:
                    for _, row in sb.iterrows():
                        name_map[row["ts_code"].split(".")[0]] = row["name"]
            except: pass

            log("📊 获取行情快照...")
            price_map = {}; pct_map = {}; turnover_map = {}; volratio_map = {}; circ_map = {}
            try:
                daily_df = pro.daily(trade_date=actual_date,
                                     fields="ts_code,close,pct_chg,turnover_rate,vol")
                attempts = 0
                while (daily_df is None or len(daily_df) < 10) and attempts < 10:
                    actual_date = (datetime.strptime(actual_date, "%Y%m%d")
                                   - timedelta(days=1)).strftime("%Y%m%d")
                    daily_df = pro.daily(trade_date=actual_date,
                                         fields="ts_code,close,pct_chg,turnover_rate,vol")
                    attempts += 1
                log(f"✅ 行情日期：{actual_date}")
                if daily_df is not None and len(daily_df) > 0:
                    basic_df = pro.daily_basic(trade_date=actual_date,
                                               fields="ts_code,volume_ratio,circ_mv")
                    for _, row in daily_df.iterrows():
                        c6 = row["ts_code"].split(".")[0]
                        price_map[c6] = float(row.get("close", 0) or 0)
                        pct_map[c6] = float(row.get("pct_chg", 0) or 0)
                        turnover_map[c6] = float(row.get("turnover_rate", 0) or 0)
                    if basic_df is not None:
                        for _, row in basic_df.iterrows():
                            c6 = row["ts_code"].split(".")[0]
                            volratio_map[c6] = float(row.get("volume_ratio", 0) or 0)
                            circ_map[c6] = float(row.get("circ_mv", 0) or 0) / 10000
            except Exception as e:
                log(f"⚠️ 行情快照失败：{e}")

            log(f"📈 拉取K线（{len(clean_codes)} 只）...")
            candidates = []
            try: hs300_chg = screener_core.get_hs300_change(actual_date)
            except: hs300_chg = 0.0
            for code in clean_codes:
                kdf = screener_core.fetch_kline(code, screener_core.KLINE_DAYS, actual_date)
                log(f"  K线 {code} {'✅' if kdf is not None else '❌'}")
                try:
                    sc, hits = screener_core._score_one(
                        {"pct_chg": pct_map.get(code, 0.0),
                         "vol_ratio": volratio_map.get(code, 0.0),
                         "turnover": turnover_map.get(code, 0.0)}, hs300_chg, kdf)
                except: sc, hits = 0, []
                candidates.append({
                    "code": code, "name": name_map.get(code, code),
                    "price": price_map.get(code, 0.0), "pct_chg": pct_map.get(code, 0.0),
                    "vol_ratio": volratio_map.get(code, 0.0), "turnover": turnover_map.get(code, 0.0),
                    "circ_cap_yi": circ_map.get(code, 0.0),
                    "stage1_score": sc, "stage1_hits": hits, "_hist": kdf,
                })

            log(f"💰 拉取资金流向...")
            ff_results = screener_core.fetch_fund_flows(candidates, actual_date)
            log("🧮 综合打分...")
            combined = []
            for rec in candidates:
                sc = screener_core.score_fund_flow(
                    rec["code"], ff_results.get(rec["code"]), rec.get("_hist"), rec.get("circ_cap_yi"))
                combined.append({**rec, **sc})
            combined.sort(key=lambda x: x["total"], reverse=True)
            result_list = [{k: _safe(v) for k, v in r.items() if not k.startswith("_")} for r in combined]
            with _tasks_lock:
                _tasks[task_id].update({"result": result_list, "status": "done",
                                         "actual_date": actual_date, "combined": combined})
            log(f"🎉 完成！分析 {len(result_list)} 只，正在生成 Excel...")
            threading.Thread(target=_generate_excel_async,
                             args=(task_id, combined, actual_date), daemon=True).start()
        except Exception as e:
            import traceback
            _tlog(task_id, f"❌ 出错：{e}")
            _tlog(task_id, traceback.format_exc())
            with _tasks_lock: _tasks[task_id]["status"] = "error"


# ══════════════════════════════════════════════════════════════
# 功能三：单股新闻资讯分析（轻任务 + SSE流式输出）
# ══════════════════════════════════════════════════════════════

def _run_news_analysis(task_id: str, symbol: str, sf_api_key: str):
    """新闻采集 + DeepSeek流式分析"""
    import traceback

    def log(msg): _tlog(task_id, msg)

    try:
        # ── 导入新闻模块 ────────────────────────────────
        import sys, pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).parent))

        import akshare as ak
        from openai import OpenAI
        from config.settings import ZHIPU_BASE_URL, MODEL
        from services.stock_service import (
            get_stock_meta_baostock, get_stock_meta,
            fetch_top_shareholders, fetch_monetary_policy, fetch_lhb_data,
        )
        from services.news_service import fetch_stock_news, fetch_company_news
        from services.sector_service import fetch_sector_data, fetch_sector_movement_reason
        from data_sources.baidu import fetch_portal_news
        from data_sources.sina import fetch_sina_realtime
        from data_sources.gnews_client import fetch_gnews_comprehensive
        from services.competitor_analysis import analyze_competitors
        from ai.prompt_builder import make_system, build_prompt, enrich_keywords_with_ai
        from models.data_model import ensure_dict
        from core.timeout import run_concurrent_tasks_with_progress
        from core.validator import clean_holders, clean_fund_flow

        # ── 初始化AI客户端 ───────────────────────────────
        client = OpenAI(api_key=sf_api_key, base_url="https://api.siliconflow.cn/v1")
        log(f"✅ SiliconFlow API 就绪")

        # ── 元信息 ────────────────────────────────────────
        log(f"🔍 获取股票元信息...")
        meta = get_stock_meta_baostock(symbol)
        if not meta["name"]:
            meta = get_stock_meta(ak, symbol)
        name = meta.get("name", symbol)
        industry = meta.get("industry", "")
        log(f"✓ {name} | {industry}")

        # ── AI扩展关键词 ─────────────────────────────────
        _set_subtask(task_id, "AI关键词扩展", "running")
        log("🧠 AI扩展关键词...")
        meta = enrich_keywords_with_ai(client, meta)
        keyword_dict = meta.get("keyword_dict", {})
        # 存入task供前端展示
        with _tasks_lock:
            if task_id in _tasks:
                _tasks[task_id]["keywords_data"] = keyword_dict
                _tasks[task_id]["symbol_label"] = f"{symbol}_{name}"
        # 打印关键词摘要到日志
        for cat, words in keyword_dict.items():
            if words:
                log(f"  📌 {cat}：{' / '.join(str(w) for w in words[:6])}")
        _set_subtask(task_id, "AI关键词扩展", "done")

        # ── 并发采集数据 ─────────────────────────────────
        log("📡 并发采集多维数据...")
        _set_subtask(task_id, "个股新闻", "running")
        _set_subtask(task_id, "板块数据", "running")
        _set_subtask(task_id, "宏观新闻", "running")
        _set_subtask(task_id, "货币政策", "running")
        _set_subtask(task_id, "实时行情", "running")
        _set_subtask(task_id, "龙虎榜数据", "running")
        _set_subtask(task_id, "大股东", "running")
        keywords = meta.get("keywords", [])
        industry_filter_kws = list(dict.fromkeys(
            keyword_dict.get("行业短词", [])[:6] + keyword_dict.get("热词", [])[:4]))
        sector_keywords = list(dict.fromkeys(
            keyword_dict.get("行业短词", [])[:6]
            + keyword_dict.get("上游短词", [])[:3]
            + keyword_dict.get("下游短词", [])[:3]
        )) or keywords

        tasks1 = {
            "stock_news":  (fetch_stock_news, (ak, symbol, name), {"industry_keywords": industry_filter_kws}),
            "sector_data": (fetch_sector_data, (ak, industry, sector_keywords, keyword_dict), {}),
            "macro_news":  (fetch_portal_news, (), {"limit": 10}),
            "monetary":    (fetch_monetary_policy, (ak,), {}),
            "spot_info":   (fetch_sina_realtime, (symbol,), {}),
            "lhb_data":    (fetch_lhb_data, (symbol, name), {}),
            "top_holders": (fetch_top_shareholders, (ak, symbol), {}),
        }

        # 子任务key → 前端显示名映射
        SUBTASK_NAME_MAP = {
            "stock_news":  "个股新闻",
            "sector_data": "板块数据",
            "macro_news":  "宏观新闻",
            "monetary":    "货币政策",
            "spot_info":   "实时行情",
            "lhb_data":    "龙虎榜数据",
            "top_holders": "大股东",
        }

        def _on_subtask_done(name, result, ok):
            """每个子任务完成时立即更新状态并写日志"""
            display = SUBTASK_NAME_MAP.get(name, name)
            # 判断状态
            if not ok or result is None:
                status = "warn"
            elif isinstance(result, dict) and not any(result.values()):
                status = "warn"
            elif isinstance(result, list) and len(result) == 0:
                status = "warn"
            else:
                status = "done"
            _set_subtask(task_id, display, status)
            # 写一条特殊日志行，前端可以解析这行来立刻更新UI
            icon = "✓" if status == "done" else "⚠"
            count = ""
            if isinstance(result, list):   count = f" {len(result)} 条"
            elif isinstance(result, dict): count = f" {len(result)} 项"
            _tlog(task_id, f"__SUBTASK_DONE__|{display}|{status}")
            _tlog(task_id, f"  {icon} {display}{count}")

        results1 = run_concurrent_tasks_with_progress(
            tasks1, max_workers=6, timeout_per_task=15, desc="采集多维数据",
            on_complete=_on_subtask_done)

        stock_news  = results1.get("stock_news")  or []
        sector_data = results1.get("sector_data") or {}
        macro_news  = results1.get("macro_news")  or []
        holder_news = []
        monetary    = results1.get("monetary")    or {}
        spot_info   = results1.get("spot_info")   or {}
        lhb_data    = results1.get("lhb_data")    or {}
        top_holders = results1.get("top_holders") or {}
        # 子任务状态已在on_complete回调中实时更新，无需批量再设置

        if not holder_news and top_holders.get("has_data") and top_holders.get("top10"):
            for item in top_holders["top10"]:
                parts = item.rsplit("(", 1)
                holder_news.append({
                    "source": "十大流通股东",
                    "holder": parts[0].strip(),
                    "ratio": parts[1].rstrip(")") if len(parts) > 1 else "",
                    "change": "",
                })
        holder_news = clean_holders(holder_news, company_name=name)
        if sector_data.get("fund_flow"):
            sector_data["fund_flow"] = clean_fund_flow(sector_data["fund_flow"])

        sector_news_list = [ensure_dict(n) for n in sector_data.get("sector_news", [])]
        stock_news_seen  = {n["title"][:20] for n in stock_news if n.get("title")}
        company_news = fetch_company_news(
            ak, symbol, name, keyword_dict, sector_news_list,
            stock_news_seen=stock_news_seen)

        # ── GNews多维搜索 ────────────────────────────────
        _set_subtask(task_id, "GNews多维搜索", "running")
        log("🔍 GNews多维搜索...")
        try:
            from config.settings import INDUSTRY_CHAIN
            upstream_kws  = []
            downstream_kws = []
            for key, chain in INDUSTRY_CHAIN.items():
                if key in industry or industry in key:
                    upstream_kws   = chain.get("upstream", [])
                    downstream_kws = chain.get("downstream", [])
                    break

            competitors_for_gnews = keyword_dict.get("竞争对手", [])[:5]
            business_kws = keyword_dict.get("下游短词", [])[:4]
            policy_kws = [f"{kw}政策" for kw in keyword_dict.get("行业短词", [])[:3]] \
                       + [f"{industry}监管", f"{industry}政策"]

            # 定义进度回调，直接写入task log
            def _gnews_progress_cb(done, total, pct, cat, query, count):
                # 构造进度行写入日志
                bar_filled = round(pct / 5)
                bar_str = '█' * bar_filled + '░' * (20 - bar_filled)
                if count == 0:
                    line1 = f"  ✗ [{cat}] {query} → 0条"
                else:
                    line1 = f"  ✓ [{cat}] {query} → {count}条"
                _tlog(task_id, line1)
                # 每5条或完成时追加进度条行
                if done % 5 == 0 or done == total:
                    _tlog(task_id, f"  多维资讯搜索: {pct}%|{bar_str}| {done}/{total}")
                # 更新GNews子任务状态
                if done < total:
                    _set_subtask(task_id, "GNews多维搜索", "running")
                else:
                    _set_subtask(task_id, "GNews多维搜索", "done")

            gnews_results = fetch_gnews_comprehensive(
                symbol=symbol, name=name,
                short_name=name[:4] if len(name) > 4 else name,
                sector=industry,
                competitors=competitors_for_gnews,
                upstream=upstream_kws[:5], downstream=downstream_kws[:5],
                policy_keywords=policy_kws,
                business_keywords=business_kws,
                progress_callback=_gnews_progress_cb,
            )

            for news_list in gnews_results.get('company_sentiment', {}).values():
                for news in news_list:
                    news['type'] = 'social'
                    company_news.append(news)

            sector_sentiment = gnews_results.get('sector_sentiment', [])
            if sector_sentiment:
                sector_data.setdefault("sector_news", []).extend(sector_sentiment)
                sector_news_list.extend([ensure_dict(n) for n in sector_sentiment])

            for chain_type, nl in gnews_results.get('industry_chain', {}).items():
                for news in nl: news['chain'] = chain_type
                if nl: sector_data.setdefault("chain_news", []).extend(nl)

            macro_news.extend(gnews_results.get('policy', []))
            log(f"✓ GNews完成（产业链{len(sector_data.get('chain_news',[]))}条）")
            _set_subtask(task_id, "GNews多维搜索", "done")
        except Exception as e:
            log(f"⚠️ GNews搜索失败: {str(e)[:60]}")
            _set_subtask(task_id, "GNews多维搜索", "warn")
            gnews_results = {}

        # ── 竞品分析（新闻情绪，不含股价） ─────────────────
        try:
            competitor_data = {}
            gnews_comp = gnews_results.get('competitors', {}) if gnews_results else {}
            competitors_for_gnews = keyword_dict.get("竞争对手", [])[:4]
            for comp in competitors_for_gnews:
                competitor_data[comp] = {'news': gnews_comp.get(comp, []), 'change': 0.0}
            if competitors_for_gnews:
                target_change = 0.0
                if spot_info:
                    try: target_change = float(spot_info.get('change', 0))
                    except: pass
                social_for_comp = [n for n in company_news if n.get("type") == "social"]
                comp_report = analyze_competitors(
                    target_symbol=symbol, target_name=name,
                    target_change=target_change,
                    target_news=stock_news + social_for_comp,
                    competitor_names=competitors_for_gnews,
                    competitor_data=competitor_data,
                )
                sector_data['competitor_report'] = comp_report
                sector_data['competitor_data'] = competitor_data
        except Exception as e:
            log(f"⚠️ 竞品分析失败: {str(e)[:50]}")

        # ── 数据去重 ─────────────────────────────────────
        _seen: set = set()
        def _dedup(lst):
            out = []
            for n in lst:
                k = n.get("title","")[:40]
                if k and k not in _seen:
                    _seen.add(k); out.append(n)
            return out
        stock_news = _dedup(stock_news)
        company_news = _dedup(company_news)
        macro_news = _dedup(macro_news)

        movement_news = sector_data.get("sector_news", [])

        total = (len(stock_news) + len(company_news)
                 + len(sector_data.get("sector_news", []))
                 + len(sector_data.get("chain_news", []))
                 + len(macro_news) + len(holder_news))
        log(f"✅ 采集完成，共{total}条数据")
        # 保存所有新闻供下载
        all_news_snapshot = {
            "stock_news":    stock_news,
            "company_news":  company_news,
            "macro_news":    macro_news,
            "holder_news":   holder_news,
            "sector_news":   sector_data.get("sector_news", []),
            "chain_news":    sector_data.get("chain_news", []),
            "lhb_data":      lhb_data,
            "spot_info":     spot_info,
            "symbol":        symbol,
            "name":          name,
            "total":         total,
        }
        with _tasks_lock:
            if task_id in _tasks:
                _tasks[task_id]["all_news"] = all_news_snapshot

        # ── 构建prompt并调用DeepSeek流式输出 ─────────────
        _set_subtask(task_id, "DeepSeek分析", "running")
        log("🤖 正在调用 DeepSeek-V3 分析...")
        from config.settings import MODEL
        prompt = build_prompt(
            symbol, meta, stock_news, macro_news, [],
            [], holder_news, company_news, sector_data,
            monetary, movement_news, spot_info, lhb_data)
        system_prompt = make_system(symbol, meta)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        # 保存prompt和meta供后续对话使用
        with _tasks_lock:
            if task_id in _tasks:
                _tasks[task_id]["prompt_context"] = prompt
                _tasks[task_id]["meta_snapshot"] = meta
                _tasks[task_id]["system_prompt"] = system_prompt

        # 流式调用，把每个chunk存入task
        full_report = ""
        start_time = time.time()
        max_stream_time = 480  # 最多8分钟
        
        try:
            stream = client.chat.completions.create(
                model=MODEL, max_tokens=8192, stream=True,
                timeout=240, messages=messages)
            
            for chunk in stream:
                # 检查超时
                if time.time() - start_time > max_stream_time:
                    log(f"⚠️ DeepSeek 分析超时（{max_stream_time}秒）")
                    break
                
                delta = chunk.choices[0].delta
                piece = getattr(delta, "content", "") or ""
                if piece:
                    full_report += piece
                    with _tasks_lock:
                        if task_id in _tasks:
                            _tasks[task_id]["news_report"] = full_report
                            # 每接收一段内容就更新一次，确保前端能看到进度
                            _tasks[task_id]["last_heartbeat"] = time.time()
        except Exception as e:
            log(f"⚠️ DeepSeek 流式调用异常: {str(e)[:60]}")
        
        # 确保任务状态被更新
        with _tasks_lock:
            if task_id in _tasks:
                _tasks[task_id]["status"] = "done"
                _tasks[task_id]["news_report"] = full_report
                _tasks[task_id]["stream_done"] = True
        
        if full_report:
            log(f"✅ 分析完成（{len(full_report)}字）")
            _set_subtask(task_id, "DeepSeek分析", "done")
        else:
            log("⚠️ 分析结果为空，可能是超时或API限制")
            _set_subtask(task_id, "DeepSeek分析", "warn")

    except Exception as e:
        _tlog(task_id, f"❌ 出错：{e}")
        _tlog(task_id, traceback.format_exc())
        with _tasks_lock:
            if task_id in _tasks:
                _tasks[task_id]["status"] = "error"
                _tasks[task_id]["stream_done"] = True


# ══════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════

def _estimate_avg_runtime(kind: str) -> float:
    durations = []
    with _tasks_lock:
        for t in _tasks.values():
            if (t["kind"] == kind and t["status"] == "done"
                    and t.get("started_at") and t.get("finished_at")):
                d = t["finished_at"] - t["started_at"]
                if 10 < d < 3600: durations.append(d)
    return sum(durations) / len(durations) if durations else 300.0


# ══════════════════════════════════════════════════════════════
# Flask 路由
# ══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return send_file("index.html")

@app.route("/api/strategies")
def api_strategies():
    return jsonify({"strategies": list_strategies()})

# ── 功能一：资金选股 ────────────────────────────────────────

@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.json or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify({"error": "请填写 Tushare Token"}), 400
    task_id = _new_task("heavy")
    qsize = _heavy_queue.qsize()
    avg_sec = _estimate_avg_runtime("heavy")
    pos = qsize + 1
    msg = "⏳ 即将开始..." if pos == 1 else f"⏳ 排队第{pos}位，预计等待约{int(avg_sec * qsize / 60)+1}分钟..."
    _tlog(task_id, msg)
    _submit_heavy(task_id, _run_screener,
                  (token, data.get("date", ""), data.get("proxy", ""),
                   data.get("strategy_id", "capital_flow")))
    return jsonify({"task_id": task_id})

# ── 功能二：单股资金分析 ────────────────────────────────────

@app.route("/api/run_single", methods=["POST"])
def api_run_single():
    data = request.json or {}
    token = (data.get("token") or "").strip()
    codes = data.get("codes", [])
    if not token: return jsonify({"error": "请填写 Tushare Token"}), 400
    if not codes: return jsonify({"error": "请提供至少一只股票代码"}), 400
    task_id = _new_task("light")
    _tlog(task_id, "⏳ 即将开始分析...")
    _submit_light(task_id, _run_single_analysis,
                  (token, codes, data.get("date", ""), data.get("proxy", "")))
    return jsonify({"task_id": task_id})

# ── 功能三：新闻资讯分析 ────────────────────────────────────

@app.route("/api/run_news", methods=["POST"])
def api_run_news():
    data = request.json or {}
    symbol = (data.get("symbol") or "").strip()
    sf_key = (data.get("sf_api_key") or "").strip()
    if not symbol: return jsonify({"error": "请填写股票代码"}), 400
    if not sf_key: return jsonify({"error": "请填写硅基流动 API Key"}), 400
    task_id = _new_task("light")
    _tlog(task_id, f"⏳ 开始采集 {symbol} 的新闻数据...")
    _submit_light(task_id, _run_news_analysis, (symbol, sf_key))
    return jsonify({"task_id": task_id})

@app.route("/api/news_stream/<task_id>")
def api_news_stream(task_id):
    """SSE接口：实时推送AI分析报告流"""
    def generate():
        last_len = 0
        timeout = 300  # 最多等5分钟
        start = time.time()
        while time.time() - start < timeout:
            with _tasks_lock:
                t = _tasks.get(task_id)
            if not t:
                yield f"data: {json.dumps({'error': '任务不存在'})}\n\n"
                break
            report = t.get("news_report") or ""
            done = t.get("stream_done", False)
            status = t.get("status")
            # 推送新增内容
            if len(report) > last_len:
                new_text = report[last_len:]
                last_len = len(report)
                yield f"data: {json.dumps({'text': new_text})}\n\n"
            if done or status in ("done", "error"):
                yield f"data: {json.dumps({'done': True, 'status': status})}\n\n"
                break
            time.sleep(0.2)
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── 通用接口 ────────────────────────────────────────────────

@app.route("/api/status/<task_id>")
def api_status(task_id):
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task: return jsonify({"error": "任务不存在"}), 404
    now = time.time()
    started = task.get("started_at")
    elapsed = int(now - started) if started else 0
    return jsonify({
        "status": task["status"],
        "queue_pos": task.get("queue_pos", 0),
        "elapsed": elapsed,
        "log": task["log"],
        "has_result": task["result"] is not None,
        "has_report": bool(task.get("news_report")),
        "subtasks": task.get("subtasks", {}),
        "keywords_data": task.get("keywords_data"),
    })

@app.route("/api/heartbeat/<task_id>", methods=["POST"])
def api_heartbeat(task_id):
    with _tasks_lock:
        t = _tasks.get(task_id)
        if t: t["last_heartbeat"] = time.time()
    return jsonify({"ok": True})

@app.route("/api/result/<task_id>")
def api_result(task_id):
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task or task["result"] is None:
        return jsonify({"error": "结果未就绪"}), 404
    return jsonify({"result": task["result"]})

@app.route("/api/excel_status/<task_id>")
def api_excel_status(task_id):
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task: return jsonify({"error": "任务不存在"}), 404
    return jsonify({"ready": task.get("excel_ready", False)})

@app.route("/api/excel/<task_id>")
def api_excel(task_id):
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task or task.get("excel") is None:
        return jsonify({"error": "Excel 未就绪"}), 404
    return send_file(
        io.BytesIO(task["excel"]),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"analysis_{task.get('actual_date','result')}.xlsx")



# ── 功能三扩展：多轮对话 ────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
def api_chat():
    """基于已分析任务的上下文进行多轮对话"""
    data = request.json or {}
    task_id  = (data.get("task_id") or "").strip()
    user_msg = (data.get("message") or "").strip()
    sf_key   = (data.get("sf_api_key") or "").strip()

    if not task_id or not user_msg:
        return jsonify({"error": "缺少task_id或message"}), 400

    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        return jsonify({"error": "任务不存在或已过期"}), 404
    if not task.get("news_report"):
        return jsonify({"error": "分析报告尚未生成"}), 400

    prompt_ctx   = task.get("prompt_context", "")
    system_p     = task.get("system_prompt", "")
    first_report = task.get("news_report", "")
    chat_hist    = task.get("chat_history", [])

    # 使用task对应的sf_key，或前端传入的key
    api_key = sf_key or ""
    if not api_key:
        return jsonify({"error": "请提供硅基流动API Key"}), 400

    def generate():
        from openai import OpenAI
        from config.settings import MODEL
        client = OpenAI(api_key=api_key, base_url="https://api.siliconflow.cn/v1")

        # 构建消息：system + 数据上下文(user) + 首次报告(assistant) + 历史对话 + 新问题
        messages = [{"role": "system", "content": system_p}]
        if prompt_ctx:
            messages.append({"role": "user", "content": prompt_ctx})
        if first_report:
            messages.append({"role": "assistant", "content": first_report})
        # 追加历史对话（最近6轮，避免超长）
        messages.extend(chat_hist[-12:])
        messages.append({"role": "user", "content": user_msg})

        reply = ""
        try:
            stream = client.chat.completions.create(
                model=MODEL, max_tokens=4096, stream=True,
                timeout=90, messages=messages)
            for chunk in stream:
                piece = getattr(chunk.choices[0].delta, "content", "") or ""
                if piece:
                    reply += piece
                    yield f"data: {json.dumps({'text': piece})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)[:100]})}\n\n"

        # 保存对话历史
        with _tasks_lock:
            t = _tasks.get(task_id)
            if t is not None:
                t["chat_history"].append({"role": "user", "content": user_msg})
                t["chat_history"].append({"role": "assistant", "content": reply})

        yield f"data: {json.dumps({'done': True})}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/chat_history/<task_id>")
def api_chat_history(task_id):
    """获取对话历史"""
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify({"history": task.get("chat_history", [])})



@app.route("/api/report_pdf/<task_id>")
def api_report_pdf(task_id):
    """用fpdf2生成白底中文PDF投资简报"""
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task or not task.get("news_report"):
        return jsonify({"error": "报告未就绪"}), 404

    report_text  = task["news_report"]
    symbol_label = task.get("symbol_label", "report")

    # 跨平台字体查找
    _FONT_REG_CANDIDATES = [
        # Linux / Render (apt: fonts-noto-cjk)
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        # Windows
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simkai.ttf",
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    _FONT_BOLD_CANDIDATES = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    FONT_REG  = next((p for p in _FONT_REG_CANDIDATES  if os.path.exists(p)), None)
    FONT_BOLD = next((p for p in _FONT_BOLD_CANDIDATES if os.path.exists(p)), None)

    # 确保fpdf2已安装
    try:
        from fpdf import FPDF, XPos, YPos
    except ImportError:
        import subprocess, sys
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "fpdf2", "-q"])
            from fpdf import FPDF, XPos, YPos
        except Exception as install_err:
            app.logger.error(f"fpdf2安装失败: {install_err}")
            txt = io.BytesIO(task["news_report"].encode("utf-8"))
            return send_file(txt, mimetype="text/plain; charset=utf-8",
                             as_attachment=True,
                             download_name=f"报告_{symbol_label}_{time.strftime('%Y%m%d')}.txt")

    try:
        from fpdf import FPDF, XPos, YPos
        import re as _re

        has_cn = FONT_REG is not None and os.path.exists(FONT_REG)

        # 单一PDF对象（含页脚的子类）
        class ReportPDF(FPDF):
            def footer(self):
                self.set_y(-13)
                fn = "body" if has_cn else "Helvetica"
                self.set_font(fn, size=8)
                self.set_text_color(160, 160, 160)
                self.cell(0, 5,
                    f"A股量化分析平台 v2 · DeepSeek-V3 · {time.strftime('%Y-%m-%d %H:%M')} · 仅供参考，不构成投资建议",
                    align="C")

        pdf = ReportPDF()
        pdf.set_margins(left=18, top=16, right=18)
        if has_cn:
            pdf.add_font("body", fname=FONT_REG)
            b_path = FONT_BOLD if (FONT_BOLD and os.path.exists(FONT_BOLD)) else FONT_REG
            pdf.add_font("bold", fname=b_path)
        pdf.set_auto_page_break(auto=True, margin=18)
        pdf.add_page()

        fn_b = "body" if has_cn else "Helvetica"
        fn_h = "bold" if has_cn else "Helvetica"
        if not has_cn:
            # 没有中文字体，回退为HTML下载
            html_content = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>投资简报 {symbol_label}</title>
<style>body{{font-family:sans-serif;max-width:800px;margin:40px auto;line-height:1.8;color:#1e293b}}
h1{{color:#1e293b;border-bottom:2px solid #2563eb;padding-bottom:8px}}
h2{{color:#2563eb;margin-top:24px}} p{{margin:8px 0;color:#334155}}
.meta{{color:#64748b;font-size:14px;margin-bottom:24px}}</style></head>
<body><pre style="white-space:pre-wrap;font-family:sans-serif;line-height:1.8">{report_text}</pre>
<p style="color:#94a3b8;font-size:12px;margin-top:40px">A股量化分析平台 · {time.strftime("%Y-%m-%d %H:%M")} · 仅供参考</p>
</body></html>"""
            html_buf = io.BytesIO(html_content.encode("utf-8"))
            return send_file(html_buf, mimetype="text/html; charset=utf-8",
                             as_attachment=True,
                             download_name=f"投资简报_{symbol_label}_{time.strftime('%Y%m%d_%H%M')}.html")

        def write(text, bold=False, size=11, color=(51,65,85), indent=0, lh=7):
            pdf.set_font(fn_h if bold else fn_b, size=size)
            pdf.set_text_color(*color)
            if indent:
                pdf.set_x(18 + indent)
                pdf.multi_cell(pdf.w - 36 - indent, lh, text,
                               new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            else:
                pdf.multi_cell(0, lh, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        for raw in report_text.split("\n"):
            s = raw.strip()
            # 清理Markdown标记
            s = _re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
            s = _re.sub(r"\*([^*]+)\*",       r"\1", s)
            s = _re.sub(r"`([^`]+)`",            r"\1", s)

            if not s:
                pdf.ln(3); continue

            if raw.startswith("# "):
                if pdf.get_y() > 240: pdf.add_page()
                write(s.lstrip("# "), bold=True, size=17, color=(30,41,59), lh=10)
                pdf.set_draw_color(37, 99, 235); pdf.set_line_width(0.7)
                pdf.line(18, pdf.get_y(), pdf.w - 18, pdf.get_y())
                pdf.ln(5)

            elif raw.startswith("## "):
                if pdf.get_y() > 255: pdf.add_page()
                pdf.ln(3)
                write(s.lstrip("# "), bold=True, size=13, color=(37,99,235), lh=8)
                pdf.ln(1)

            elif raw.startswith("### "):
                write(s.lstrip("# "), bold=True, size=11, color=(30,41,59))

            elif raw.startswith("> "):
                pdf.set_fill_color(241, 245, 249)
                pdf.set_font(fn_b, size=10)
                pdf.set_text_color(71, 85, 105)
                pdf.set_x(22)
                pdf.multi_cell(pdf.w - 40, 7, s.lstrip("> "), fill=True,
                               new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.ln(1)

            elif _re.match(r"^[-*•] |^\d+\. ", raw):
                bullet = _re.sub(r"^[-*•] |^\d+\. ", "", s)
                if any(k in bullet for k in ["做多","利多","看涨","正面","多头"]):
                    col = (22, 163, 74)
                elif any(k in bullet for k in ["做空","利空","看跌","回避","空头","跌停"]):
                    col = (220, 38, 38)
                else:
                    col = (51, 65, 85)
                write("• " + bullet, size=10, color=col, indent=6)

            elif raw.startswith("---"):
                pdf.set_draw_color(226, 232, 240); pdf.set_line_width(0.3)
                pdf.line(18, pdf.get_y(), pdf.w - 18, pdf.get_y())
                pdf.ln(4)

            else:
                if "【推断】" in s:   col = (180, 120, 0)
                elif "【回避】" in s: col = (180,  40, 40)
                elif "【做多】" in s: col = (22,  130, 70)
                elif "👉" in s:       col = (99,  102, 241)
                else:                 col = (51,   65, 85)
                write(s, size=10.5, color=col)

        # ── 附录：新闻条目 ──────────────────────────────
        all_news = task.get("all_news")
        if all_news:
            pdf.add_page()
            # 附录标题
            write("附录：新闻数据来源", bold=True, size=15, color=(30,41,59), lh=10)
            pdf.set_draw_color(37,99,235); pdf.set_line_width(0.7)
            pdf.line(18, pdf.get_y(), pdf.w-18, pdf.get_y())
            pdf.ln(5)
            write(f"股票：{all_news.get('name','')}（{all_news.get('symbol','')}）  "
                  f"共采集：{all_news.get('total',0)}条  "
                  f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}",
                  size=9, color=(100,116,139), lh=6)
            pdf.ln(4)

            # 各类新闻分节渲染
            sections = [
                ("个股新闻（东方财富）", all_news.get("stock_news", [])),
                ("公司舆情", all_news.get("company_news", [])),
                ("宏观新闻", all_news.get("macro_news", [])),
                ("板块行业新闻", all_news.get("sector_news", [])),
                ("产业链新闻", all_news.get("chain_news", [])),
            ]

            for sec_title, news_list in sections:
                if not news_list:
                    continue
                # 分节标题
                pdf.ln(3)
                if pdf.get_y() > 260:
                    pdf.add_page()
                write(f"▌ {sec_title}（{len(news_list)}条）",
                      bold=True, size=11, color=(37,99,235), lh=7)
                pdf.set_draw_color(226,232,240); pdf.set_line_width(0.25)
                pdf.line(18, pdf.get_y(), pdf.w-18, pdf.get_y())
                pdf.ln(3)

                for i, n in enumerate(news_list[:40], 1):  # 每类最多40条
                    if not isinstance(n, dict): continue
                    title = n.get("title", n.get("holder", "")).strip()
                    if not title or len(title) < 4: continue
                    if title.startswith("http"): continue
                    ts  = (n.get("time") or n.get("date") or "")[:16]
                    src = n.get("source", "")
                    url = n.get("url", "")
                    # 过滤Google RSS URL
                    if url and "news.google.com/rss/articles" in url:
                        url = ""

                    if pdf.get_y() > 270:
                        pdf.add_page()

                    # 序号 + 标题行
                    pdf.set_font(fn_b, size=9)
                    pdf.set_text_color(51, 65, 85)
                    line_text = f"{i:>3}. [{ts}] {title}"
                    if src:
                        line_text += f"  [{src}]"
                    pdf.multi_cell(0, 6, line_text,
                                   new_x="LMARGIN", new_y="NEXT")
                    # URL（仅可读链接）
                    if url:
                        pdf.set_font(fn_b, size=8)
                        pdf.set_text_color(148, 163, 184)
                        pdf.set_x(22)
                        pdf.multi_cell(pdf.w-40, 5, url,
                                       new_x="LMARGIN", new_y="NEXT")
                    pdf.set_text_color(51, 65, 85)

            # 龙虎榜附录
            lhb = all_news.get("lhb_data", {})
            if lhb and lhb.get("has_lhb"):
                pdf.ln(3)
                if pdf.get_y() > 250: pdf.add_page()
                write("▌ 龙虎榜明细", bold=True, size=11, color=(37,99,235), lh=7)
                pdf.set_draw_color(226,232,240); pdf.set_line_width(0.25)
                pdf.line(18, pdf.get_y(), pdf.w-18, pdf.get_y())
                pdf.ln(3)
                concl = lhb.get("conclusion", "")
                if concl:
                    write(concl, size=9.5, color=(51,65,85))
                for det in lhb.get("details", [])[:10]:
                    seat  = det.get("seat","")
                    buy   = det.get("buy","N/A")
                    sell  = det.get("sell","N/A")
                    write(f"  · {seat}  买入：{buy}  卖出：{sell}",
                          size=9, color=(100,116,139))

            # 大股东附录
            holders = all_news.get("holder_news", [])
            if holders:
                pdf.ln(3)
                if pdf.get_y() > 250: pdf.add_page()
                write("▌ 十大流通股东", bold=True, size=11, color=(37,99,235), lh=7)
                pdf.set_draw_color(226,232,240); pdf.set_line_width(0.25)
                pdf.line(18, pdf.get_y(), pdf.w-18, pdf.get_y())
                pdf.ln(3)
                for h in holders[:10]:
                    if not isinstance(h, dict): continue
                    name_h  = h.get("holder","")
                    ratio   = h.get("ratio","")
                    change  = h.get("change","")
                    if name_h:
                        write(f"  · {name_h}  {ratio}  {change}",
                              size=9, color=(100,116,139))

            # 附录页脚说明
            pdf.ln(6)
            pdf.set_draw_color(226,232,240); pdf.set_line_width(0.3)
            pdf.line(18, pdf.get_y(), pdf.w-18, pdf.get_y())
            pdf.ln(4)
            write("本附录由 A股量化分析平台自动采集生成，新闻内容来源于东方财富、新浪财经、GNews等公开渠道，"
                  "仅供参考，不构成投资建议。转载或引用请核实原始来源。",
                  size=8, color=(148,163,184))

        out_bytes = bytes(pdf.output())
        buf  = io.BytesIO(out_bytes)
        fname = f"投资简报_{symbol_label}_{time.strftime('%Y%m%d_%H%M')}.pdf"
        return send_file(buf, mimetype="application/pdf",
                         as_attachment=True, download_name=fname)

    except Exception as e:
        import traceback
        app.logger.error(f"PDF生成失败: {e}\n{traceback.format_exc()}")
        # 回退txt时也记录错误，方便排查
        txt = io.BytesIO(report_text.encode("utf-8"))
        return send_file(txt, mimetype="text/plain; charset=utf-8",
                         as_attachment=True,
                         download_name=f"report_{time.strftime('%Y%m%d')}.txt")


@app.route("/api/news_download/<task_id>")
def api_news_download(task_id):
    """下载所有采集到的新闻（纯文本格式）"""
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task or not task.get("all_news"):
        return jsonify({"error": "新闻数据不存在或已过期"}), 404

    d = task["all_news"]
    symbol = d.get("symbol", "")
    name   = d.get("name", "")
    total  = d.get("total", 0)
    lines  = []
    lines.append(f"{'='*70}")
    lines.append(f"A股量化分析平台 · 新闻采集汇总")
    lines.append(f"股票：{name}（{symbol}）  采集时间：{time.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"总计：{total}条")
    lines.append(f"{'='*70}\n")

    def fmt_section(title, news_list, key_map=None):
        if not news_list: return
        lines.append(f"\n{'─'*60}")
        lines.append(f"【{title}】共{len(news_list)}条")
        lines.append(f"{'─'*60}")
        for i, n in enumerate(news_list, 1):
            if isinstance(n, dict):
                t = n.get("title", n.get("holder",""))
                ts = n.get("time", n.get("date",""))[:16] if n.get("time") or n.get("date") else ""
                src = n.get("source","")
                url = n.get("url","")
                if key_map:
                    t = " | ".join(str(n.get(k,"")) for k in key_map if n.get(k))
                # 跳过空标题和纯URL标题
                if not t or len(t) < 4: continue
                if t.startswith("http://") or t.startswith("https://"): continue
                lines.append(f"  {i:>3}. [{ts}] {t}  [{src}]")
                # 只保留非Google-RSS的可读URL
                if url and "news.google.com/rss/articles" not in url:
                    lines.append(f"       {url}")
            else:
                if str(n).startswith("http"): continue  # 跳过纯URL行
                lines.append(f"  {i:>3}. {n}")

    fmt_section("个股新闻·东方财富", d.get("stock_news",[]))
    fmt_section("公司舆情·社会新闻", d.get("company_news",[]))
    fmt_section("宏观新闻", d.get("macro_news",[]))
    fmt_section("板块新闻", d.get("sector_news",[]))

    chain_news = d.get("chain_news",[])
    up   = [n for n in chain_news if n.get("chain")=="upstream"]
    down = [n for n in chain_news if n.get("chain")=="downstream"]
    pol  = [n for n in chain_news if n.get("chain")=="policy"]
    fmt_section("产业链·上游新闻", up)
    fmt_section("产业链·下游新闻", down)
    fmt_section("产业链·政策新闻", pol)

    lhb = d.get("lhb_data",{})
    if lhb.get("has_lhb"):
        lines.append(f"\n{'─'*60}")
        lines.append(f"【龙虎榜】{lhb.get('conclusion','')}")
        for det in lhb.get("details",[]):
            lines.append(f"  · {det.get('seat','')} | 买:{det.get('buy','N/A')} 卖:{det.get('sell','N/A')}")

    spot = d.get("spot_info",{})
    if spot:
        lines.append(f"\n{'─'*60}")
        lines.append(f"【实时行情】价格:{spot.get('price','')}  涨跌:{spot.get('change','')}%")

    holders = d.get("holder_news",[])
    if holders:
        fmt_section("十大流通股东", holders, key_map=["holder","ratio","change"])

    lines.append(f"\n{'='*70}")
    lines.append("本文件由 A股量化分析平台 自动生成，仅供参考，不构成投资建议")
    lines.append(f"{'='*70}")

    content = "\n".join(lines)
    buf = io.BytesIO(content.encode("utf-8"))
    fname = f"新闻数据_{symbol}_{name}_{time.strftime('%Y%m%d_%H%M')}.txt"
    return send_file(buf, mimetype="text/plain; charset=utf-8",
                     as_attachment=True, download_name=fname)


@app.route("/api/queue_status")
def api_queue_status():
    now = time.time()
    running = []; waiting = []
    with _tasks_lock:
        for tid, t in _tasks.items():
            if t["status"] == "running":
                started = t.get("started_at")
                running.append({"task_id": tid, "kind": t["kind"],
                                 "elapsed": int(now - started) if started else 0})
            elif t["status"] == "queued":
                waiting.append({"task_id": tid, "kind": t["kind"],
                                 "queue_pos": t.get("queue_pos", 0)})
    return jsonify({"running": running, "waiting": waiting,
                    "avg_runtime_sec": int(_estimate_avg_runtime("heavy"))})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
