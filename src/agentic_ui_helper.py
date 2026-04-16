"""Agentic UI 纯函数辅助：便于单测，避免在 app.py 里堆规则逻辑。"""
from __future__ import annotations

from typing import Any


def infer_risk_level(score_deduction: int) -> str:
    d = int(score_deduction or 0)
    if d >= 10:
        return "high"
    if d >= 5:
        return "medium"
    return "low"


def build_action_buttons(agent_state: dict[str, Any], draft_report: dict[str, Any]) -> list[str]:
    return [x["label"] for x in build_action_specs(agent_state, draft_report)]


def build_action_specs(agent_state: dict[str, Any], draft_report: dict[str, Any]) -> list[dict[str, Any]]:
    """返回可执行动作规范，供 UI 做 LUI/GUI/workflow 联动。"""
    actions: list[dict[str, Any]] = []
    asset_hits = list(agent_state.get("asset_hits") or [])
    events = list(agent_state.get("memory_events") or [])
    rps = list((draft_report or {}).get("risk_points") or [])

    high_risk = sorted(
        rps,
        key=lambda x: int((x or {}).get("score_deduction", 0) or 0),
        reverse=True,
    )
    high_risk_n = sum(
        1
        for rp in rps
        if infer_risk_level(int((rp or {}).get("score_deduction", 0) or 0)) == "high"
    )

    if not asset_hits:
        actions.append(
            {
                "kind": "chat",
                "label": "补齐财务缺口",
                "prompt": "请基于当前风险点，给我一份“财务与经营数据缺口清单”，按优先级列出并给出补齐路径。",
            }
        )
    if high_risk_n >= 2:
        actions.append(
            {
                "kind": "chat",
                "label": "开启极限施压模拟",
                "prompt": "请你扮演最苛刻投资人，围绕我们当前最高风险点进行连续追问，并给出每轮改进建议。",
            }
        )
    if events:
        actions.append(
            {
                "kind": "chat",
                "label": "生成本轮错题复训计划",
                "prompt": "根据本轮记忆事件，输出 7 天复训计划：每日训练题、通过标准、复盘模板。",
            }
        )
    if high_risk:
        top = high_risk[0] or {}
        rid = str(top.get("_rid") or "")
        risk_type = str(top.get("risk_type") or "高风险点")
        actions.append(
            {
                "kind": "focus_risk",
                "label": f"定位风险：{risk_type}",
                "prompt": f"请针对风险“{risk_type}”给出 3 条可执行修复动作。",
                "target_rid": rid,
            }
        )

    # 一键工作流触发入口（简报）
    actions.append(
        {
            "kind": "briefing",
            "label": "一键生成会前简报",
            "prompt": "生成会前简报",
        }
    )

    if not actions:
        actions.append(
            {
                "kind": "chat",
                "label": "生成下次会前简报",
                "prompt": "请根据当前复盘结果生成下一次会前准备清单。",
            }
        )

    # UI 约束：最多 4 个按钮，优先保留多样动作类型
    out: list[dict[str, Any]] = []
    kinds_seen: set[str] = set()
    for a in actions:
        k = str(a.get("kind") or "")
        if len(out) >= 4:
            break
        if k in kinds_seen and k in {"chat", "focus_risk"} and len(actions) > 4:
            continue
        out.append(a)
        kinds_seen.add(k)
    return out[:4]


def resolve_focus_target(draft_report: dict[str, Any], target_rid: str) -> dict[str, Any] | None:
    if not target_rid:
        return None
    for rp in list((draft_report or {}).get("risk_points") or []):
        if str((rp or {}).get("_rid") or "") == target_rid:
            return rp
    return None
