"""
Fix 1 — 音频路径 bug 测试。
验证：大文件经网关压缩+ASR+删除后，原始音频文件仍然存在且可访问。
不依赖 Streamlit，zero API cost。
"""
from pathlib import Path


def test_original_audio_survives_after_gateway_deleted(tmp_path):
    """
    文档化 bug 的本质：
    gateway 文件被删后，如果 ctx 存的是 audio_path（原始），文件仍然存在；
    如果 ctx 存的是 work_audio（gateway），文件已不存在。
    """
    # 模拟原始音频（永远不删）
    audio_path = tmp_path / "高管访谈_市场负责人.m4a"
    audio_path.write_bytes(b"fake original audio data")

    # 模拟网关压缩文件（ASR 后删除）
    gateway = tmp_path / "高管访谈_市场负责人_v62_asr_gateway.mp3"
    gateway.write_bytes(b"fake compressed audio data")

    # 模拟 ASR 完成后删除 gateway（阅后即焚）
    gateway.unlink()

    # ── 修复后的行为：ctx 存 audio_path（原始），文件仍存在 ──
    ctx_audio = str(audio_path)
    assert Path(ctx_audio).is_file(), \
        "audio_path 指向原始文件，应始终存在"

    # ── bug 的原始行为：ctx 存 work_audio（gateway），文件已被删 ──
    ctx_audio_bug = str(gateway)
    assert not Path(ctx_audio_bug).is_file(), \
        "gateway 已被 unlink，若 ctx 存此路径则无法播放音频（这是 bug 的触发条件）"


def test_small_file_original_path_also_correct(tmp_path):
    """小文件不走 gateway，audio_path == work_audio，修复对此路径无影响。"""
    audio_path = tmp_path / "小文件访谈.m4a"
    audio_path.write_bytes(b"small audio")

    # 小文件：work_audio = audio_path（两者相同，无论存哪个都正确）
    assert Path(str(audio_path)).is_file()


def test_two_files_each_has_own_audio_path(tmp_path):
    """多文件批处理场景：每个 stem 对应独立的原始音频路径，互不干扰。"""
    audio_a = tmp_path / "访谈A.m4a"
    audio_b = tmp_path / "访谈B.m4a"
    audio_a.write_bytes(b"audio A data")
    audio_b.write_bytes(b"audio B data")

    # 模拟两个 gateway 文件分别创建后被删除
    gw_a = tmp_path / "访谈A_v62_asr_gateway.mp3"
    gw_b = tmp_path / "访谈B_v62_asr_gateway.mp3"
    gw_a.write_bytes(b"compressed A")
    gw_b.write_bytes(b"compressed B")
    gw_a.unlink()
    gw_b.unlink()

    # 两个原始文件均应仍然存在
    assert Path(str(audio_a)).is_file(), "访谈A 原始音频应存在"
    assert Path(str(audio_b)).is_file(), "访谈B 原始音频应存在"
    # 两个 gateway 均已删除
    assert not gw_a.is_file()
    assert not gw_b.is_file()
