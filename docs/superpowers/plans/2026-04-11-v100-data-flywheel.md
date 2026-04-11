# V10.0 数据飞轮加速版 — 实施计划

> **版本目标**：从"单机本地工具"进化为"可协作、可度量、可演化的知识资产系统"，不以云端化为首要目标，以**数据可分析性**和**多人共享**为首要目标。

---

## ⚡ 新 AI 接手必读（30 秒上手）

| 项目 | 当前事实 |
|------|---------|
| **背景对话** | 主理人讨论了数据飞轮方向，见 `V8.6_Data_Flywheel_Blueprint.md` 和本计划文件 |
| **当前版本** | V9.6.5，全量 `pytest tests/` → **271 passed** |
| **本次目标** | V10.0-Phase1：共享记忆协议 + Analytics JSON 导出层 |
| **执行原则** | 铁律一至五全部适用；TDD 优先；不触碰 schema.py；不引入云端依赖 |
| **整体进度** | 见下方各 Task 的 `> 执行状态` 行 |

---

## 整体文件地图

| 文件 | 改动内容 | Task |
|------|---------|------|
| `src/runtime_paths.py` | 新增 `get_memory_root()` / `get_asr_cache_root()`，读 `MEMORY_ROOT` / `CACHE_ROOT` 环境变量 | Task 1 |
| `src/memory_engine.py` | `default_store_dir()` 改用 `get_memory_root()` | Task 1 |
| `src/disk_asr_cache.py` | `get_default_cache_dir()` 改用 `get_asr_cache_root()` | Task 1 |
| `tests/test_v100_shared_memory.py` | Task 1 的全部测试（新建） | Task 1 |
| `src/analytics_exporter.py` | 新建：锁定时静默导出 analytics JSON | Task 2 |
| `app.py` | `_v3_finalize_stem` 末尾调 analytics_exporter | Task 2 |
| `tests/test_v100_analytics.py` | Task 2 的全部测试（新建） | Task 2 |
| `src/memory_engine.py` | `get_company_dashboard_stats` 增加飞轮速度指标 | Task 3 |
| `app.py` | Dashboard 增加命中率趋势图 | Task 3 |
| `tests/test_v100_flywheel_metrics.py` | Task 3 的全部测试（新建） | Task 3 |
| `AGENTS.md` | 握手区更新发版号、测试数量 | 每 Task 后 |
| `ARCHITECTURE.md` | 新增模块说明 | Task 2 完成后 |
| `.env.example`（如不存在则新建） | 说明 `MEMORY_ROOT` / `CACHE_ROOT` 用法 | Task 1 后 |

---

## Task 1：共享记忆协议（MEMORY_ROOT_OVERRIDE）

> **执行状态（2026-04-11）**：✅ **完成** — 全部 Steps 通过；`pytest tests/` → **285 passed**。
> **测试状态**：`tests/test_v100_shared_memory.py` **14/14 passed**；顺带修复 `test_v86_memory_engine.py` 的 mock 路径（1行）。
> **阻断因素**：无。已可正式使用 MEMORY_ROOT / CACHE_ROOT 环境变量。

**目标**：通过 `.env` 文件中的 `MEMORY_ROOT` / `CACHE_ROOT` 环境变量，让同事把 `.executive_memory/` 和 `.asr_cache/` 目录挂载到共享网盘，**零代码改动实现多人共享**。

**设计原则**：
- 不改任何现有函数签名
- 不改任何调用方（`app.py`、`job_pipeline.py`）
- 环境变量未设置时行为**完全与之前相同**（零回归风险）
- `store_dir` 显式传参仍优先（测试继续隔离）

**文件改动范围**：
- `src/runtime_paths.py`：新增 2 个函数（约 15 行）
- `src/memory_engine.py`：改 `default_store_dir()` 1 行
- `src/disk_asr_cache.py`：改 `get_default_cache_dir()` 1 行

---

- [x] **Step 1：新建测试文件，写失败测试** ✅

```python
# tests/test_v100_shared_memory.py
"""
V10.0 共享记忆协议测试 — MEMORY_ROOT / CACHE_ROOT 环境变量覆盖。
运行：pytest tests/test_v100_shared_memory.py -v
所有测试 zero API cost，无外部依赖。
"""
from __future__ import annotations
import os, sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


class TestGetMemoryRoot:
    """get_memory_root() 在有无环境变量时的行为。"""

    def test_default_returns_writable_root_subdir(self):
        """未设置 MEMORY_ROOT 时，返回 get_writable_app_root() / .executive_memory。"""
        import runtime_paths
        from runtime_paths import get_memory_root, get_writable_app_root
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEMORY_ROOT", None)
            result = get_memory_root()
        expected = get_writable_app_root() / ".executive_memory"
        assert result == expected

    def test_env_var_overrides_default(self, tmp_path):
        """设置 MEMORY_ROOT 后，返回值为该路径。"""
        from runtime_paths import get_memory_root
        override = str(tmp_path / "shared_memory")
        with patch.dict(os.environ, {"MEMORY_ROOT": override}):
            result = get_memory_root()
        assert result == Path(override)

    def test_env_var_path_is_absolute(self, tmp_path):
        """MEMORY_ROOT 返回的 Path 与传入字符串一致（绝对路径）。"""
        from runtime_paths import get_memory_root
        override = str(tmp_path / "net_share" / "memory")
        with patch.dict(os.environ, {"MEMORY_ROOT": override}):
            assert get_memory_root() == Path(override)


class TestGetAsrCacheRoot:
    """get_asr_cache_root() 在有无环境变量时的行为。"""

    def test_default_returns_writable_root_subdir(self):
        """未设置 CACHE_ROOT 时，返回 get_writable_app_root() / .asr_cache。"""
        from runtime_paths import get_asr_cache_root, get_writable_app_root
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CACHE_ROOT", None)
            result = get_asr_cache_root()
        expected = get_writable_app_root() / ".asr_cache"
        assert result == expected

    def test_env_var_overrides_default(self, tmp_path):
        """设置 CACHE_ROOT 后，返回值为该路径。"""
        from runtime_paths import get_asr_cache_root
        override = str(tmp_path / "shared_cache")
        with patch.dict(os.environ, {"CACHE_ROOT": override}):
            result = get_asr_cache_root()
        assert result == Path(override)


class TestMemoryEngineDefaultStoreDir:
    """memory_engine.default_store_dir() 随 MEMORY_ROOT 变化。"""

    def test_default_store_dir_without_env(self):
        """无 MEMORY_ROOT 时，default_store_dir 使用可写根。"""
        import memory_engine
        from runtime_paths import get_writable_app_root
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEMORY_ROOT", None)
            # 重新导入以清除模块级缓存（若有）
            result = memory_engine.default_store_dir()
        assert result == get_writable_app_root() / ".executive_memory"

    def test_default_store_dir_with_env(self, tmp_path):
        """设置 MEMORY_ROOT 后，default_store_dir 使用覆盖路径。"""
        import memory_engine
        override = str(tmp_path / "override_memory")
        with patch.dict(os.environ, {"MEMORY_ROOT": override}):
            result = memory_engine.default_store_dir()
        assert result == Path(override)


class TestDiskAsrCacheRoot:
    """disk_asr_cache.get_default_cache_dir() 随 CACHE_ROOT 变化。"""

    def test_default_cache_dir_without_env(self):
        """无 CACHE_ROOT 时，使用可写根下 .asr_cache。"""
        import disk_asr_cache
        from runtime_paths import get_writable_app_root
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CACHE_ROOT", None)
            result = disk_asr_cache.get_default_cache_dir()
        assert result == get_writable_app_root() / ".asr_cache"

    def test_default_cache_dir_with_env(self, tmp_path):
        """设置 CACHE_ROOT 后，返回覆盖路径。"""
        import disk_asr_cache
        override = str(tmp_path / "shared_asr_cache")
        with patch.dict(os.environ, {"CACHE_ROOT": override}):
            result = disk_asr_cache.get_default_cache_dir()
        assert result == Path(override)


class TestEndToEndSharedMemoryWrite:
    """验证在 MEMORY_ROOT 覆盖下，记忆实际写入到指定路径。"""

    def test_memory_written_to_override_dir(self, tmp_path):
        """设置 MEMORY_ROOT 后，append_executive_memory 写入到该目录。"""
        import uuid as uuid_mod
        import memory_engine
        from schema import ExecutiveMemory

        shared_dir = tmp_path / "net_drive" / "executive_memory"
        mem = ExecutiveMemory(
            uuid=str(uuid_mod.uuid4()),
            tag="测试高管",
            raw_text="营收口径前后不一致",
            correction="统一口径：以管理层口径为准",
            weight=1.0,
            risk_type="数据矛盾",
            updated_at="2026-04-11T00:00:00Z",
            hit_count=0,
        )

        with patch.dict(os.environ, {"MEMORY_ROOT": str(shared_dir)}):
            # 传入 store_dir=None 触发 default_store_dir() 路径
            memory_engine.append_executive_memory(
                "测试机构", "测试高管", mem, store_dir=None
            )

        # 文件应在 shared_dir 下，而不是默认可写根下
        assert shared_dir.is_dir(), "共享目录应被自动创建"
        files = list(shared_dir.rglob("*.json"))
        assert len(files) >= 1, "记忆文件应写入共享目录"
```

- [x] **Step 2：运行测试，确认失败** ✅（11 FAILED，3 PASSED — 符合预期）

```
cd D:/AI_Workspaces/AI_Pitch_Coach
pytest tests/test_v100_shared_memory.py -v
```
预期：`TestGetMemoryRoot::test_env_var_overrides_default` 等 **FAILED**（函数尚不存在）

- [x] **Step 3：实现 `src/runtime_paths.py` — 新增 2 个函数** ✅（`get_memory_root` + `get_asr_cache_root`，各约 12 行）

在文件末尾追加：

```python
def get_memory_root() -> Path:
    """
    记忆库根目录。
    优先读 MEMORY_ROOT 环境变量（供同事挂载共享网盘使用）；
    未设置时回退到 get_writable_app_root() / '.executive_memory'。
    """
    override = os.environ.get("MEMORY_ROOT", "").strip()
    if override:
        return Path(override)
    return get_writable_app_root() / ".executive_memory"


def get_asr_cache_root() -> Path:
    """
    ASR 磁盘缓存根目录。
    优先读 CACHE_ROOT 环境变量；
    未设置时回退到 get_writable_app_root() / '.asr_cache'。
    """
    override = os.environ.get("CACHE_ROOT", "").strip()
    if override:
        return Path(override)
    return get_writable_app_root() / ".asr_cache"
```

- [x] **Step 4：更新 `src/memory_engine.py` — `default_store_dir()` 改 1 行** ✅（顺带修复 `test_v86_memory_engine.py` mock 路径）

将：
```python
def default_store_dir() -> Path:
    """默认可写根下的 `.executive_memory` 目录。"""
    return get_writable_app_root() / EXECUTIVE_MEMORY_SUBDIR
```
改为：
```python
def default_store_dir() -> Path:
    """
    记忆库根目录。
    优先读 MEMORY_ROOT 环境变量（多人共享网盘场景）；
    未设置时为可写根下的 `.executive_memory` 目录。
    """
    from runtime_paths import get_memory_root  # 已在模块顶部导入 runtime_paths，此处直接用
    return get_memory_root()
```

> **注意**：`runtime_paths` 已在文件顶部 `from runtime_paths import get_writable_app_root` 导入。
> 直接在顶部导入中追加 `get_memory_root` 更规范，避免函数内 import。

- [x] **Step 5：更新 `src/disk_asr_cache.py` — `get_default_cache_dir()` 改 1 行** ✅

将：
```python
def get_default_cache_dir() -> Path:
    """返回默认磁盘缓存目录（writable_app_root/.asr_cache）。"""
    return get_writable_app_root() / ".asr_cache"
```
改为：
```python
def get_default_cache_dir() -> Path:
    """
    ASR 磁盘缓存目录。
    优先读 CACHE_ROOT 环境变量（多人共享网盘场景）；
    未设置时为可写根下的 `.asr_cache` 目录。
    """
    return get_asr_cache_root()
```

> **注意**：在文件顶部导入中追加 `get_asr_cache_root`：
> `from runtime_paths import get_writable_app_root, get_asr_cache_root`

- [x] **Step 6：运行 Task 1 专项测试，确认全绿** ✅ → **14 passed**

- [x] **Step 7：全量回归** ✅ → **285 passed**（原 271 + 14 新增）

- [x] **Step 8：Commit** ✅

```bash
git add src/runtime_paths.py src/memory_engine.py src/disk_asr_cache.py tests/test_v100_shared_memory.py
git commit -m "feat(V10.0-Task1): MEMORY_ROOT/CACHE_ROOT 环境变量覆盖，支持共享网盘多人协作"
```

---

## Task 2：Analytics JSON 导出层

> **执行状态（2026-04-11）**：✅ **完成** — 全部 Steps 通过；`pytest tests/` → **303 passed**。
> **测试状态**：`tests/test_v100_analytics.py` **18/18 passed**。
> **已改文件**：`src/analytics_exporter.py`（新建）、`app.py`（import + 1行调用）。

**目标**：每次锁定生成 HTML 时，在 JSON 落盘旁边静默生成一份 `{stem}_analytics.json`，结构化记录得分、风险分布、精炼次数，为后续跨公司分析打基础。

**设计原则**：
- 不改 schema.py，不改 AnalysisReport 结构
- 不改 report_builder.py
- 仅在 `_v3_finalize_stem`（app.py）锁定环节末尾增加一个非阻断调用
- 导出失败静默跳过，不影响主流程

**输出格式**：
```json
{
  "session_id": "uuid-v4",
  "generated_at": "2026-04-11T10:00:00Z",
  "version": "V10.0",
  "company_id": "迪策资本",
  "speaker_id": "李志新",
  "recording_label": "迪策资本-李志新20260108.m4a",
  "total_score": 72,
  "total_risk_count": 5,
  "risk_breakdown": {
    "严重": {"count": 1, "total_deduction": 15},
    "一般": {"count": 3, "total_deduction": 12},
    "轻微": {"count": 1, "total_deduction": 3}
  },
  "risk_types": ["数据矛盾", "回避类", "数据矛盾", "估值追问", "表达模糊"],
  "refinement_count": 2,
  "ai_miss_count": 0,
  "stage1_truncated": false
}
```

**文件改动**：
- `src/analytics_exporter.py`（新建，约 80 行）
- `app.py`：在 `_v3_finalize_stem` 末尾追加约 10 行

**测试文件**：`tests/test_v100_analytics.py`

---

- [ ] **Step 1：写失败测试（新建 tests/test_v100_analytics.py）**
- [ ] **Step 2：运行，确认失败**
- [ ] **Step 3：实现 src/analytics_exporter.py**
- [ ] **Step 4：在 app.py _v3_finalize_stem 末尾调用**
- [ ] **Step 5：专项测试全绿**
- [ ] **Step 6：全量回归**
- [ ] **Step 7：更新 ARCHITECTURE.md（新增 analytics_exporter 模块行）**
- [ ] **Step 8：Commit**

```bash
git commit -m "feat(V10.0-Task2): 锁定时静默导出 analytics JSON，打通数据可分析性"
```

---

## Task 3：飞轮速度看板

> **执行状态（2026-04-11）**：✅ **完成** — 全部 Steps 通过；`pytest tests/` → **318 passed**。
> **测试状态**：`tests/test_v100_flywheel_metrics.py` **15/15 passed**。
> **已改文件**：`src/memory_engine.py`（新增 `_build_flywheel_metrics` + `flywheel_metrics` 字段）、`app.py`（Dashboard 新增飞轮速度看板区块）。

**目标**：在 V9.0 Dashboard 基础上增加"飞轮速度指数"视图：
- 命中率趋势（有历史画像 vs 无历史画像批次得分对比）
- 记忆库贡献榜（hit_count TOP 10 Plotly 柱状图）
- 本月提炼数量统计

**文件改动**：
- `src/memory_engine.py`：`get_company_dashboard_stats` 增加 `flywheel_metrics` 子键
- `app.py`：Dashboard Tab 增加飞轮视图
- `tests/test_v100_flywheel_metrics.py`

---

- [x] **Step 1-8**：全部完成 ✅（见执行状态）

---

## 不在本计划范围

- 云端数据库（Supabase 等）
- 多用户权限管理
- 正则脱敏管道增强
- schema.py 字段变更

---

## 快速回归命令

```bash
# Task 1 专项
pytest tests/test_v100_shared_memory.py -v

# Task 2 专项
pytest tests/test_v100_analytics.py -v

# 全量回归（跳过集成测试）
pytest tests/ -m "not integration" -q
```

---

*计划创建时间：2026-04-11 · 主理人：波总 · 执行 AI：Claude Sonnet 4.6*
