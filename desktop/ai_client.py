#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ai_client.py — OpenAI 兼容大模型客户端 / 复杂任务拆解 / 每日每周评价。

契约见 ``docs/08-待办模块升级设计.md`` 第 4 节。本模块只依赖标准库 + requests，
不导入 PyQt6，也不依赖 ``core`` / 桌面界面，因此可以在纯 Python 环境里单独测试：

    python -m unittest tests.test_integrations -v

对外接口：

- ``OpenAICompatibleClient(base_url, api_key, model, timeout=40)``
  ``chat(messages, temperature=0.3, json_mode=False) -> str``
- ``plan_complex_task(client, task_text, today, horizon_days=14) -> dict``
- ``evaluate(kind, client, context) -> str``

错误约定：

- ``AIError``：未配置 API Key、网络失败、响应结构异常等「调用失败」；
- ``PlanError``（``AIError`` 的子类）：模型返回的规划 JSON 解析失败、字段缺失或
  日期越界等「内容不符合契约」。

调用方（桌面端）对 ``AIError`` / ``PlanError`` 都应降级处理，不阻塞主功能。
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

import requests

__all__ = [
    "AIError",
    "DEFAULT_TIMEOUT",
    "OpenAICompatibleClient",
    "PlanError",
    "evaluate",
    "plan_complex_task",
]

#: 默认请求超时（秒）。大模型拆解任务通常需要 10–30 秒。
DEFAULT_TIMEOUT = 40.0

#: ```json ... ``` / ``` ... ``` 代码围栏（模型经常不听话地包一层围栏）。
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.DOTALL)


class AIError(RuntimeError):
    """AI 调用失败（未配置 Key、网络错误、响应结构异常）。"""


class PlanError(AIError):
    """模型规划结果不符合契约（JSON 解析失败 / 字段缺失 / 日期越界）。"""


class OpenAICompatibleClient:
    """最小可用的 OpenAI 兼容 Chat Completions 客户端。

    只实现 ``POST {base_url}/chat/completions``：DeepSeek、OpenAI、Moonshot、
    本地 vLLM / Ollama 等兼容接口都能直接复用。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
        session: Optional[requests.Session] = None,
    ) -> None:
        #: 去掉末尾的 /，避免拼出 ``//chat/completions``
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "").strip()
        self.timeout = timeout
        #: 测试时可注入假 session（与 core.GitHubGistStore 的做法一致）
        self.session = session or requests.Session()

    # -- 基础 -------------------------------------------------------------
    @property
    def configured(self) -> bool:
        """是否已经配置好 base_url / api_key / model。"""
        return bool(self.base_url and self.api_key and self.model)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # -- 对话 -------------------------------------------------------------
    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        temperature: float = 0.3,
        json_mode: bool = False,
    ) -> str:
        """发送一轮对话，返回首个 choice 的文本内容。

        ``json_mode=True`` 时附带 ``response_format={"type": "json_object"}``。
        未配置 / 网络失败 / 响应结构异常都会抛 ``AIError``。
        """
        if not self.base_url:
            raise AIError("尚未配置 AI Base URL（ai_base_url）")
        if not self.api_key:
            raise AIError("尚未配置 AI API Key（ai_api_key）")
        if not self.model:
            raise AIError("尚未配置 AI 模型（ai_model）")
        if not messages:
            raise ValueError("messages 不能为空")

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [dict(message) for message in messages],
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = self.session.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise AIError(f"AI 请求失败：{exc}") from exc

        status = int(getattr(response, "status_code", 0) or 0)
        if status >= 400:
            detail = ""
            try:
                error = response.json().get("error") or {}
                if isinstance(error, Mapping):
                    detail = str(error.get("message") or "").strip()
            except Exception:  # noqa: BLE001 - 错误响应体不一定是 JSON
                detail = ""
            suffix = f"：{detail}" if detail else ""
            raise AIError(f"AI 接口返回 HTTP {status}{suffix}")

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIError("AI 响应缺少 choices[0].message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise AIError("AI 返回内容为空")
        return content.strip()


# ---------------------------------------------------------------------------
# 复杂任务拆解
# ---------------------------------------------------------------------------

_GRANULARITY_HINTS = {
    "concise": "3–5 个",
    "standard": "5–8 个",
    "detailed": "8–12 个",
}

_PLAN_SYSTEM_TEMPLATE = (
    "你是一名中文任务规划助手。请把用户描述的复杂任务拆分成可执行的子任务，"
    "并给出每个子任务的目标完成日期与预计耗时。\n"
    "只输出一个 JSON 对象，不要输出任何解释、前缀或 Markdown 代码围栏。JSON 结构：\n"
    '{{"title": "任务标题", "steps": [{{"text": "子任务内容", '
    '"due_date": "YYYY-MM-DD", "estimate_minutes": 30}}], "note": "给用户的提醒"}}\n'
    "约束：\n"
    "1. due_date 必须在 {start} 到 {end} 之间（含端点），格式 YYYY-MM-DD；\n"
    "2. estimate_minutes 是 0 到 480 的整数（0 表示未估）；\n"
    "3. steps 至少 1 个，按执行顺序排列；\n"
    "4. note 可以为空字符串；\n"
    "5. 遵循用户的额外偏好：\n{extra}"
)


def _coerce_day(value: Any) -> Optional[_dt.date]:
    """把 date / datetime / ISO 日期字符串统一成 ``date``；无法识别返回 None。"""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return _dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def _extract_json(raw: str) -> Any:
    """从模型回复中提取 JSON：容忍纯 JSON、```json 围栏、前后夹带说明文字。"""
    if not isinstance(raw, str) or not raw.strip():
        raise PlanError("AI 返回为空，无法解析规划结果")
    candidate = raw.strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    fence = _FENCE_RE.search(candidate)
    if fence:
        fenced = fence.group(1).strip()
        try:
            return json.loads(fenced)
        except json.JSONDecodeError:
            candidate = fenced

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise PlanError(f"AI 规划结果不是合法 JSON：{exc}") from exc

    raise PlanError("AI 规划结果中没有找到 JSON 对象")


def _parse_minutes(value: Any, index: int) -> int:
    """校验 estimate_minutes：0–480 的整数；缺失视为 0，非法值抛 PlanError。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0
    if isinstance(value, bool):
        raise PlanError(f"第 {index} 个子任务的 estimate_minutes 不是整数")
    try:
        minutes = int(float(value))
    except (TypeError, ValueError) as exc:
        raise PlanError(f"第 {index} 个子任务的 estimate_minutes 不是整数：{value!r}") from exc
    if minutes < 0:
        raise PlanError(f"第 {index} 个子任务的 estimate_minutes 不能为负数")
    return min(minutes, 480)


def _parse_plan(raw: str, start: _dt.date, end: _dt.date, task_text: str) -> Dict[str, Any]:
    """把模型返回文本解析成契约里的 plan dict，任何格式问题抛 ``PlanError``。"""
    data = _extract_json(raw)
    if not isinstance(data, Mapping):
        raise PlanError("AI 规划结果不是 JSON 对象")

    title = str(data.get("title") or "").strip()
    if not title:
        # 模型偶尔漏掉 title：退化为用户输入，不影响拆解结果
        title = task_text.strip()[:80]

    steps_raw = data.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise PlanError("AI 规划结果缺少非空的 steps 列表")

    steps: List[Dict[str, Any]] = []
    for index, item in enumerate(steps_raw, start=1):
        if not isinstance(item, Mapping):
            raise PlanError(f"第 {index} 个子任务不是 JSON 对象")
        text = str(item.get("text") or "").strip()
        if not text:
            raise PlanError(f"第 {index} 个子任务缺少 text")
        due_day = _coerce_day(item.get("due_date"))
        if due_day is None:
            raise PlanError(
                f"第 {index} 个子任务的 due_date 不是合法日期：{item.get('due_date')!r}"
            )
        if due_day < start or due_day > end:
            raise PlanError(
                f"第 {index} 个子任务的日期 {due_day.isoformat()} 超出 "
                f"{start.isoformat()} ~ {end.isoformat()} 范围"
            )
        steps.append(
            {
                "text": text,
                "due_date": due_day.isoformat(),
                "estimate_minutes": _parse_minutes(item.get("estimate_minutes"), index),
            }
        )

    note = str(data.get("note") or "").strip()
    return {"title": title, "steps": steps, "note": note}


def plan_complex_task(
    client: OpenAICompatibleClient,
    task_text: str,
    today: Any = None,
    horizon_days: int = 14,
    options: Any = None,
) -> Dict[str, Any]:
    """调用大模型把复杂任务拆成带目标日期的子任务。

    ``options`` 可选：
    - ``daily_minutes``：每天可投入的分钟数（0/缺省=不限）；
    - ``granularity``：``concise`` / ``standard`` / ``detailed``；
    - ``allow_weekends``：是否允许把子任务排在周末（默认 True）。

    返回 ``{"title": str, "steps": [{"text", "due_date", "estimate_minutes"}], "note": str}``。
    ``due_date`` 保证落在 ``today .. today + horizon_days`` 之内；模型输出格式错误 /
    日期越界时抛 ``PlanError``，网络失败抛 ``AIError``。
    """
    if client is None:
        raise AIError("AI 客户端未初始化")
    task = str(task_text or "").strip()
    if not task:
        raise PlanError("任务描述不能为空")

    start = _coerce_day(today) if today is not None else _dt.date.today()
    if start is None:
        raise PlanError(f"today 不是合法日期：{today!r}")
    try:
        horizon = int(horizon_days)
    except (TypeError, ValueError) as exc:
        raise PlanError(f"horizon_days 不是整数：{horizon_days!r}") from exc
    if horizon < 0:
        raise PlanError("horizon_days 不能为负数")
    end = start + _dt.timedelta(days=horizon)

    opts = options if isinstance(options, Mapping) else {}
    try:
        daily_minutes = max(0, int(opts.get("daily_minutes") or 0))
    except (TypeError, ValueError):
        daily_minutes = 0
    granularity = str(opts.get("granularity") or "standard").strip().lower()
    if granularity not in _GRANULARITY_HINTS:
        granularity = "standard"
    allow_weekends = bool(opts.get("allow_weekends", True))
    extra_lines = [
        f"   - 每天可投入时间：{'不限' if daily_minutes <= 0 else str(daily_minutes) + ' 分钟'}",
        f"   - 拆分粒度：{_GRANULARITY_HINTS[granularity]}子任务",
        "   - 周末安排："
        + ("周末也可以安排" if allow_weekends else "只在周一至周五安排（跳过周末）"),
    ]
    if not allow_weekends:
        extra_lines.append("   - 所有 due_date 必须是周一到周五；若原日期落在周末，顺延到下一个工作日")

    system = _PLAN_SYSTEM_TEMPLATE.format(
        start=start.isoformat(), end=end.isoformat(), extra="\n".join(extra_lines)
    )
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"今天的日期是 {start.isoformat()}。请规划这个任务：{task}\n"
                f"偏好：{extra_lines[0]}；{extra_lines[1]}；{extra_lines[2]}。"
            ),
        },
    ]
    raw = client.chat(messages, temperature=0.2, json_mode=True)
    plan = _parse_plan(raw, start, end, task)
    if not allow_weekends:
        for step in plan["steps"]:
            try:
                day = _dt.date.fromisoformat(step["due_date"])
            except (KeyError, ValueError):
                continue
            while day.weekday() >= 5:
                day += _dt.timedelta(days=1)
            if day > end:
                day = end
                while day.weekday() >= 5:
                    day -= _dt.timedelta(days=1)
            step["due_date"] = day.isoformat()
    return plan


# ---------------------------------------------------------------------------
# 每日 / 每周评价
# ---------------------------------------------------------------------------

_EVALUATE_SYSTEM = (
    "你是一名中文效率助手。请根据用户给出的数据，用 2–5 句中文给出简短、具体、"
    "不带说教口吻的评价，最后用一句话给出 1 条可执行的改进建议。"
    "不要编造数据，不要输出 Markdown 标题或列表符号。"
)


def _context_text(context: Any) -> str:
    """把 context（dict / list / str / None）转成给模型的纯文本。"""
    if context is None:
        return "（无数据）"
    if isinstance(context, str):
        return context.strip() or "（无数据）"
    if isinstance(context, Mapping):
        try:
            return json.dumps(context, ensure_ascii=False, indent=2, default=str)
        except (TypeError, ValueError):
            return str(context)
    return str(context)


def evaluate(kind: str, client: OpenAICompatibleClient, context: Any = None) -> str:
    """生成每日（``kind="day"``）或每周（``kind="week"``）中文评价。

    返回 2–5 句评价 + 1 条改进建议；未配置 / 网络失败抛 ``AIError``，
    ``kind`` 非法抛 ``ValueError``。调用方负责降级。
    """
    kind_text = str(kind or "").strip().lower()
    if kind_text not in ("day", "week"):
        raise ValueError("kind 必须是 'day' 或 'week'")
    if client is None:
        raise AIError("AI 客户端未初始化")

    period = "今天" if kind_text == "day" else "本周"
    user = f"周期：{period}\n数据：\n{_context_text(context)[:8000]}"
    reply = client.chat(
        [
            {"role": "system", "content": _EVALUATE_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.6,
    )
    if not isinstance(reply, str) or not reply.strip():
        raise AIError("AI 返回的评价值为空")
    return reply.strip()
