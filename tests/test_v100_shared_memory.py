"""
V10.0 共享记忆协议测试 — MEMORY_ROOT / CACHE_ROOT 环境变量覆盖。

目标：通过 .env 中的 MEMORY_ROOT / CACHE_ROOT，同事可以把记忆库和 ASR 缓存
挂载到共享网盘，实现零代码改动的多人协作。

运行：pytest tests/test_v100_shared_memory.py -v
所有测试 zero API cost，无外部依赖。
"""
from __future__ import annotations

import os
import sys
import uuid as uuid_mod
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ════════════════════════════════════════════════════════
# Task 1a — get_memory_root() 函数行为
# ════════════════════════════════════════════════════════


class TestGetMemoryRoot:
    """get_memory_root() 在有无 MEMORY_ROOT 环境变量时的行为。"""

    def test_default_returns_writable_root_subdir(self):
        """未设置 MEMORY_ROOT 时，返回 get_writable_app_root() / .executive_memory。"""
        import importlib
        import runtime_paths
        from runtime_paths import get_writable_app_root

        env = {k: v for k, v in os.environ.items() if k != "MEMORY_ROOT"}
        with patch.dict(os.environ, env, clear=True):
            result = runtime_paths.get_memory_root()

        expected = get_writable_app_root() / ".executive_memory"
        assert result == expected

    def test_env_var_overrides_default(self, tmp_path):
        """设置 MEMORY_ROOT 后，返回值为该路径（不是可写根的子目录）。"""
        import runtime_paths

        override = str(tmp_path / "shared_memory")
        with patch.dict(os.environ, {"MEMORY_ROOT": override}):
            result = runtime_paths.get_memory_root()

        assert result == Path(override)

    def test_env_var_absolute_path(self, tmp_path):
        """MEMORY_ROOT 支持任意绝对路径（模拟网盘挂载路径）。"""
        import runtime_paths

        override = str(tmp_path / "Z" / "team_share" / "executive_memory")
        with patch.dict(os.environ, {"MEMORY_ROOT": override}):
            assert runtime_paths.get_memory_root() == Path(override)

    def test_empty_env_var_falls_back_to_default(self):
        """MEMORY_ROOT 设为空字符串时，等同于未设置，回退默认路径。"""
        import runtime_paths
        from runtime_paths import get_writable_app_root

        with patch.dict(os.environ, {"MEMORY_ROOT": ""}):
            result = runtime_paths.get_memory_root()

        assert result == get_writable_app_root() / ".executive_memory"


# ════════════════════════════════════════════════════════
# Task 1b — get_asr_cache_root() 函数行为
# ════════════════════════════════════════════════════════


class TestGetAsrCacheRoot:
    """get_asr_cache_root() 在有无 CACHE_ROOT 环境变量时的行为。"""

    def test_default_returns_writable_root_subdir(self):
        """未设置 CACHE_ROOT 时，返回 get_writable_app_root() / .asr_cache。"""
        import runtime_paths
        from runtime_paths import get_writable_app_root

        env = {k: v for k, v in os.environ.items() if k != "CACHE_ROOT"}
        with patch.dict(os.environ, env, clear=True):
            result = runtime_paths.get_asr_cache_root()

        expected = get_writable_app_root() / ".asr_cache"
        assert result == expected

    def test_env_var_overrides_default(self, tmp_path):
        """设置 CACHE_ROOT 后，返回值为该路径。"""
        import runtime_paths

        override = str(tmp_path / "shared_asr_cache")
        with patch.dict(os.environ, {"CACHE_ROOT": override}):
            result = runtime_paths.get_asr_cache_root()

        assert result == Path(override)

    def test_empty_env_var_falls_back_to_default(self):
        """CACHE_ROOT 设为空字符串时，回退默认路径。"""
        import runtime_paths
        from runtime_paths import get_writable_app_root

        with patch.dict(os.environ, {"CACHE_ROOT": ""}):
            result = runtime_paths.get_asr_cache_root()

        assert result == get_writable_app_root() / ".asr_cache"


# ════════════════════════════════════════════════════════
# Task 1c — memory_engine.default_store_dir() 联动
# ════════════════════════════════════════════════════════


class TestMemoryEngineDefaultStoreDir:
    """memory_engine.default_store_dir() 随 MEMORY_ROOT 变化。"""

    def test_default_store_dir_without_env(self):
        """无 MEMORY_ROOT 时，default_store_dir 使用可写根下 .executive_memory。"""
        import memory_engine
        from runtime_paths import get_writable_app_root

        env = {k: v for k, v in os.environ.items() if k != "MEMORY_ROOT"}
        with patch.dict(os.environ, env, clear=True):
            result = memory_engine.default_store_dir()

        assert result == get_writable_app_root() / ".executive_memory"

    def test_default_store_dir_with_env(self, tmp_path):
        """设置 MEMORY_ROOT 后，default_store_dir 使用覆盖路径。"""
        import memory_engine

        override = str(tmp_path / "override_memory")
        with patch.dict(os.environ, {"MEMORY_ROOT": override}):
            result = memory_engine.default_store_dir()

        assert result == Path(override)


# ════════════════════════════════════════════════════════
# Task 1d — disk_asr_cache.get_default_cache_dir() 联动
# ════════════════════════════════════════════════════════


class TestDiskAsrCacheRoot:
    """disk_asr_cache.get_default_cache_dir() 随 CACHE_ROOT 变化。"""

    def test_default_cache_dir_without_env(self):
        """无 CACHE_ROOT 时，使用可写根下 .asr_cache。"""
        import disk_asr_cache
        from runtime_paths import get_writable_app_root

        env = {k: v for k, v in os.environ.items() if k != "CACHE_ROOT"}
        with patch.dict(os.environ, env, clear=True):
            result = disk_asr_cache.get_default_cache_dir()

        assert result == get_writable_app_root() / ".asr_cache"

    def test_default_cache_dir_with_env(self, tmp_path):
        """设置 CACHE_ROOT 后，返回覆盖路径。"""
        import disk_asr_cache

        override = str(tmp_path / "shared_asr_cache")
        with patch.dict(os.environ, {"CACHE_ROOT": override}):
            result = disk_asr_cache.get_default_cache_dir()

        assert result == Path(override)


# ════════════════════════════════════════════════════════
# Task 1e — 端到端：记忆实际写入覆盖路径
# ════════════════════════════════════════════════════════


class TestEndToEndSharedMemoryWrite:
    """设置 MEMORY_ROOT 后，记忆确实写入到指定目录，而非默认可写根。"""

    def _make_mem(self, raw_text: str = "营收口径前后不一致") -> "ExecutiveMemory":
        from schema import ExecutiveMemory

        return ExecutiveMemory(
            uuid=str(uuid_mod.uuid4()),
            tag="测试高管",
            raw_text=raw_text,
            correction="统一口径：以管理层口径为准",
            weight=1.0,
            risk_type="数据矛盾",
            updated_at="2026-04-11T00:00:00Z",
            hit_count=0,
        )

    def test_memory_written_to_override_dir(self, tmp_path):
        """设置 MEMORY_ROOT 后，append_executive_memory(store_dir=None) 写入到共享目录。"""
        import memory_engine

        shared_dir = tmp_path / "net_drive" / "executive_memory"
        mem = self._make_mem()

        with patch.dict(os.environ, {"MEMORY_ROOT": str(shared_dir)}):
            # store_dir=None → 触发 default_store_dir() → 读 MEMORY_ROOT
            memory_engine.append_executive_memory(
                "测试机构", "测试高管", mem, store_dir=None
            )

        # 文件应在 shared_dir 下，而不是默认可写根下
        assert shared_dir.is_dir(), "共享目录应被自动创建"
        json_files = list(shared_dir.rglob("*.json"))
        assert len(json_files) >= 1, "记忆文件应写入共享目录"

    def test_memory_not_written_to_default_when_overridden(self, tmp_path):
        """MEMORY_ROOT 覆盖时，文件写入共享目录，而非默认可写根（隔离验证）。"""
        import memory_engine

        shared_dir = tmp_path / "shared"
        default_dir = tmp_path / "fake_default"  # 用 tmp_path 隔离，避免污染真实项目目录

        mem = self._make_mem("隔离测试记忆_应出现在共享目录")

        # 用 patch 模拟 get_writable_app_root 指向 fake_default，与 MEMORY_ROOT 隔离
        with patch("runtime_paths.get_writable_app_root", return_value=default_dir):
            with patch.dict(os.environ, {"MEMORY_ROOT": str(shared_dir)}):
                memory_engine.append_executive_memory(
                    "隔离测试机构", "隔离测试高管", mem, store_dir=None
                )

        # 文件应在 shared_dir 下（MEMORY_ROOT 优先）
        assert shared_dir.is_dir(), "MEMORY_ROOT 目录应被创建"
        # 默认路径（fake_default）下不应有文件（MEMORY_ROOT 覆盖生效）
        assert not (default_dir / ".executive_memory").is_dir(), \
            "覆盖路径生效时，默认路径不应产生目录"

    def test_explicit_store_dir_still_wins(self, tmp_path):
        """显式传入 store_dir 时，优先级高于 MEMORY_ROOT 环境变量。"""
        import memory_engine

        explicit_dir = tmp_path / "explicit_dir"
        override_dir = tmp_path / "override_dir"
        mem = self._make_mem("显式 store_dir 测试")

        with patch.dict(os.environ, {"MEMORY_ROOT": str(override_dir)}):
            memory_engine.append_executive_memory(
                "测试机构3", "显式测试", mem, store_dir=explicit_dir
            )

        # 显式路径有文件
        assert any(explicit_dir.rglob("*.json")), "显式 store_dir 应优先被写入"
        # 覆盖路径无文件
        assert not override_dir.exists(), "MEMORY_ROOT 在显式 store_dir 存在时不应被写入"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
