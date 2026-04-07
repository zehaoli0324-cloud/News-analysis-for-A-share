"""
智谱 GLM 客户端  v2.5
- 流式调用、重试、错误诊断（不变）
- 新增 validate_analysis_output()：检查输出是否包含必要决策结构
"""
import time
from typing import List, Tuple

from config.settings import (
    MODEL, MAX_TOKENS, API_TIMEOUT, API_RETRIES,
    green, red, yellow, dim, bold,
)

# 有效分析输出必须包含的关键词（至少命中其中几个）
_REQUIRED_SECTIONS = [
    "多空",      # 多空博弈
    "因果",      # 因果链
    "操作",      # 操作建议
    "触发",      # 触发条件
    "回避",      # 或"做多"/"观望"
]

_MIN_LENGTH = 300   # 有效分析最短字数（原来50）


def validate_analysis_output(text: str) -> Tuple[bool, str]:
    """
    检查 AI 输出是否包含决策性结构。
    返回 (is_valid, reason)
    """
    if not text or len(text.strip()) < _MIN_LENGTH:
        return False, f"输出过短（{len(text.strip())} 字，要求 ≥{_MIN_LENGTH}）"
    hits = [kw for kw in _REQUIRED_SECTIONS if kw in text]
    if len(hits) < 2:
        missing = [kw for kw in _REQUIRED_SECTIONS if kw not in text]
        return False, f"缺少决策结构关键词：{missing}"
    return True, "OK"


def call_glm(client, messages: List[dict],
             print_stream: bool = True) -> Tuple[str, bool]:
    """
    调用智谱 GLM，返回 (文本内容, 是否成功)。
    - 自动重试 API_RETRIES 次
    - 流式内容实时打印
    """
    last_err = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            stream = client.chat.completions.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                stream=True,
                timeout=API_TIMEOUT,
                messages=messages,
            )
            text = ""
            if print_stream:
                print("\n" + "─" * 55)
            for chunk in stream:
                delta = chunk.choices[0].delta
                piece = getattr(delta, "content", "") or ""
                if print_stream:
                    print(piece, end="", flush=True)
                text += piece
            if print_stream:
                print("\n" + "─" * 55)
            return text, True

        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            if "401" in str(e) or "authentication" in err_str:
                print(red("\n❌ API Key 无效或已过期，请检查后重新输入"))
                return "", False
            if attempt < API_RETRIES:
                print(yellow(f"\n  ⚠ 第 {attempt} 次连接失败，2s 后重试..."))
                time.sleep(2)

    err_str = str(last_err).lower()
    print(red(f"\n❌ 智谱 API 连接失败：{last_err}"))
    if any(kw in err_str for kw in ("connection", "timeout", "network")):
        print(yellow(
            "\n  💡 网络诊断建议：\n"
            "     1. 确认能访问 https://open.bigmodel.cn（浏览器测试）\n"
            "     2. 如使用代理，确保代理覆盖 Python 请求\n"
            "        Windows: set HTTPS_PROXY=http://127.0.0.1:你的端口\n"
            "     3. 尝试切换网络（手机热点等）"
        ))
    return "", False


def compress_history(history: List[dict], max_tokens: int = 8000) -> List[dict]:
    """超长时保留前2条上下文 + 最近6条，其余省略"""
    total_chars = sum(len(m["content"]) for m in history)
    if total_chars // 3 < max_tokens or len(history) <= 8:
        return history
    new_history = history[:2]
    new_history.append({"role": "user", "content": "[... 历史对话已省略 ...]"})
    new_history.extend(history[-6:])
    return new_history


def chat_loop(client, symbol: str, meta: dict,
              context_prompt: str, first_analysis: str):
    """多轮对话入口"""
    from ai.prompt_builder import make_system
    system = make_system(symbol, meta)
    history = [
        {"role": "user",      "content": context_prompt},
        {"role": "assistant", "content": first_analysis},
    ]
    print(bold("\n💬 进入对话模式（输入 q 退出 / clear 清空历史）"))
    print(dim("  示例：这只股票多空博弈谁占优？ / 触发做多的条件是什么？ / 和同行比如何？"))
    print("─" * 55)

    while True:
        try:
            user_input = input(bold("\n你：")).strip()
        except (EOFError, KeyboardInterrupt):
            print(dim("\n（已退出对话）"))
            break
        if not user_input:
            continue
        if user_input.lower() in ("q", "quit", "exit", "退出"):
            print(dim("（已退出对话）"))
            break
        if user_input.lower() == "clear":
            history = [
                {"role": "user",      "content": context_prompt},
                {"role": "assistant", "content": first_analysis},
            ]
            print(yellow("  ✓ 对话历史已清空，新闻上下文保留"))
            continue

        history.append({"role": "user", "content": user_input})
        compressed = compress_history(history)
        messages   = [{"role": "system", "content": system}] + compressed
        reply, ok  = call_glm(client, messages, print_stream=True)
        if not ok or not reply:
            history.pop()
            continue
        history.append({"role": "assistant", "content": reply})
        print(dim(f"  （已对话 {len(history) // 2} 轮）"))
