"""
P2 回归：文字稿质量三联修复——标点注入 + 说话人分段 + 对齐免疫。

问题根因（已确认）：
  A. SiliconFlow SenseVoiceSmall：词级 token 无标点（标点在 segment.text 中）
  B. 阿里云 Paraformer：diarization_enabled 未设置 → 所有词同 speaker → 无分段
  C. format_transcript_plain_by_speaker：段内不拆句 → 整段挤成一大坨

修复方案（三联）：
  1. 阿里云 API 参数加 diarization_enabled=True（返回 spk_id）
  2. _map_aliyun_paraformer_to_schema：将 sentence.text 末尾标点追加到句末词的 text 字段
  3. _build_siliconflow_segment_punct_map：从 segment.text 提取标点，映射到段末词索引
  4. _map_siliconflow_to_schema：接受 punct_map，给段末词追加标点
  5. format_transcript_plain_by_speaker：遇到句末标点就在同说话人内部换行

架构师红线（对齐免疫）：
  - TranscriptionWord.start_time / end_time / word_index 不得改变（Base64 跳转对齐）
  - 仅修改 TranscriptionWord.text 的末尾（追加标点符号），不替换、不截断
  - format_transcript_plain_by_speaker 的输出变化仅影响 UI 展示，不入 AnalysisReport

零 API 费用：所有测试用纯本地逻辑 + 手工构造结构体。

运行：pytest tests/test_v90_transcript_quality.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from schema import TranscriptionWord  # noqa: E402


# ── 测试辅助 ────────────────────────────────────────────────────────────────

def _tw(idx: int, text: str, *, spk: str = "spk_0", t0: float = 0.0, t1: float = 0.1) -> TranscriptionWord:
    return TranscriptionWord(
        word_index=idx, text=text, start_time=t0, end_time=t1, speaker_id=spk
    )


# ═══════════════════════════════════════════════════════════════════════════
# Part 1: 阿里云参数 diarization_enabled 覆盖（黑盒验证 body 结构）
# ═══════════════════════════════════════════════════════════════════════════

class TestAliyunDiarizationParam:
    """验证 _dashscope_submit_transcription_rest 的 body 包含 diarization_enabled=True。"""

    def test_request_body_contains_diarization_enabled(self):
        """
        检查函数提交给阿里云的 JSON body 中 diarization_enabled=True 已就位。
        通过 mock POST 请求拦截 body，避免真实 API 调用。
        """
        from unittest.mock import MagicMock, patch
        import json as _json
        from transcriber import _dashscope_submit_transcription_rest

        captured_body: dict = {}

        def fake_post_retry(url, *, headers, data, timeout):
            nonlocal captured_body
            captured_body = _json.loads(data)
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"output": {"task_id": "test-task-123"}}
            return resp

        with patch("transcriber._requests_post_with_retry", side_effect=fake_post_retry):
            _dashscope_submit_transcription_rest("fake_key", "oss://fake/file.mp3")

        params = captured_body.get("parameters", {})
        assert params.get("diarization_enabled") is True, (
            "diarization_enabled 必须为 True，否则阿里云不会返回 spk_id，"
            "导致多说话人无法分段"
        )

    def test_request_body_retains_punctuation_prediction(self):
        """加入 diarization_enabled 后，enable_punctuation_prediction 不得丢失。"""
        from unittest.mock import MagicMock, patch
        import json as _json
        from transcriber import _dashscope_submit_transcription_rest

        captured_body: dict = {}

        def fake_post_retry(url, *, headers, data, timeout):
            nonlocal captured_body
            captured_body = _json.loads(data)
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"output": {"task_id": "t"}}
            return resp

        with patch("transcriber._requests_post_with_retry", side_effect=fake_post_retry):
            _dashscope_submit_transcription_rest("k", "oss://x")

        params = captured_body.get("parameters", {})
        assert params.get("enable_punctuation_prediction") is True


# ═══════════════════════════════════════════════════════════════════════════
# Part 2: 阿里云句末标点注入到词级 text
# ═══════════════════════════════════════════════════════════════════════════

class TestAliyunSentencePunctInjection:
    """验证 _map_aliyun_paraformer_to_schema 将句末标点追加到最后一个词。"""

    def _make_result(self, sentences: list[dict]) -> dict:
        return {"transcripts": [{"sentences": sentences}]}

    def _make_sent(self, text: str, words_texts: list[str], spk: str = "0") -> dict:
        words = []
        for i, t in enumerate(words_texts):
            words.append({
                "begin_time": i * 500,
                "end_time": (i + 1) * 500,
                "text": t,
                "speaker_id": spk,
            })
        return {"text": text, "words": words, "speaker_id": spk}

    def test_sentence_terminal_punct_appended_to_last_word(self):
        """句末标点追加到句子最后一个词的 text 字段。"""
        from transcriber import _map_aliyun_paraformer_to_schema
        result = self._make_result([
            self._make_sent("你好世界。", ["你好", "世界"]),
        ])
        words = _map_aliyun_paraformer_to_schema(result)
        assert words[-1].text == "世界。", (
            f"最后一个词应为 '世界。'，实际是 '{words[-1].text}'"
        )
        assert words[0].text == "你好"  # 非末词不变

    def test_question_mark_appended(self):
        """问号（？）也能正确追加。"""
        from transcriber import _map_aliyun_paraformer_to_schema
        result = self._make_result([
            self._make_sent("明白了吗？", ["明白", "了吗"]),
        ])
        words = _map_aliyun_paraformer_to_schema(result)
        assert words[-1].text == "了吗？"

    def test_multiple_sentences_each_last_word_gets_punct(self):
        """多句话，每句的末词都追加该句的标点。"""
        from transcriber import _map_aliyun_paraformer_to_schema
        result = self._make_result([
            self._make_sent("今天开会。", ["今天", "开会"]),
            self._make_sent("明天汇报！", ["明天", "汇报"]),
        ])
        words = _map_aliyun_paraformer_to_schema(result)
        assert len(words) == 4
        assert words[1].text == "开会。"   # 第一句末词
        assert words[3].text == "汇报！"   # 第二句末词
        assert words[0].text == "今天"     # 非末词不变
        assert words[2].text == "明天"     # 非末词不变

    def test_no_punct_sentence_not_modified(self):
        """没有句末标点的句子：词 text 不变。"""
        from transcriber import _map_aliyun_paraformer_to_schema
        result = self._make_result([
            self._make_sent("没有标点", ["没有", "标点"]),
        ])
        words = _map_aliyun_paraformer_to_schema(result)
        assert words[-1].text == "标点"   # 无标点，不追加

    def test_timestamps_unchanged_after_punct_injection(self):
        """架构师红线：追加标点不得改变 start_time / end_time / word_index。"""
        from transcriber import _map_aliyun_paraformer_to_schema
        result = self._make_result([
            self._make_sent("数据说话。", ["数据", "说话"]),
        ])
        words = _map_aliyun_paraformer_to_schema(result)
        assert words[0].word_index == 0
        assert words[1].word_index == 1
        assert words[0].start_time == pytest.approx(0.0)
        assert words[0].end_time == pytest.approx(0.5)
        assert words[1].start_time == pytest.approx(0.5)
        assert words[1].end_time == pytest.approx(1.0)

    def test_diarization_speaker_ids_preserved(self):
        """说话人 ID 正确传递（diarization 开启时多 spk 正常映射）。"""
        from transcriber import _map_aliyun_paraformer_to_schema
        result = self._make_result([
            self._make_sent("我说。", ["我", "说"], spk="0"),
            self._make_sent("他答。", ["他", "答"], spk="1"),
        ])
        words = _map_aliyun_paraformer_to_schema(result)
        # 两组说话人 ID 必须不同
        spk_ids = {w.speaker_id for w in words}
        assert len(spk_ids) == 2, f"应有 2 个不同说话人，实际 {spk_ids}"


# ═══════════════════════════════════════════════════════════════════════════
# Part 3: SiliconFlow segment → punct_map → 词末追加
# ═══════════════════════════════════════════════════════════════════════════

class TestSiliconflowSegmentPunctMap:
    """验证 _build_siliconflow_segment_punct_map 正确从 segment.text 提取标点。"""

    def _make_seg(self, text: str, words_texts: list[str]) -> dict:
        words = []
        for i, t in enumerate(words_texts):
            words.append({"word": t, "start": float(i), "end": float(i) + 0.9})
        return {"text": text, "words": words}

    def test_single_segment_with_fullstop(self):
        """单段，句末全角句号 → 最后一个有效词的索引映射到 '。'"""
        from transcriber import _build_siliconflow_segment_punct_map
        payload = {"segments": [self._make_seg("你好世界。", ["你好", "世界"])]}
        m = _build_siliconflow_segment_punct_map(payload)
        # 2 个有效词，最后一个索引为 1
        assert m == {1: "。"}

    def test_multiple_segments_each_last_word_mapped(self):
        """两段，各自的末词都有正确映射。"""
        from transcriber import _build_siliconflow_segment_punct_map
        payload = {"segments": [
            self._make_seg("第一句。", ["第一", "句"]),
            self._make_seg("第二句！", ["第二", "句"]),
        ]}
        m = _build_siliconflow_segment_punct_map(payload)
        assert m == {1: "。", 3: "！"}  # 总共 4 个有效词，0-3，末词索引为 1 和 3

    def test_segment_without_punct_not_in_map(self):
        """没有标点的段不出现在 map 里。"""
        from transcriber import _build_siliconflow_segment_punct_map
        payload = {"segments": [
            self._make_seg("没有标点", ["没有", "标点"]),
            self._make_seg("有标点。", ["有", "标点"]),
        ]}
        m = _build_siliconflow_segment_punct_map(payload)
        # 第一段末词索引 1 无标点，第二段末词索引 3 有标点
        assert 1 not in m
        assert m.get(3) == "。"

    def test_no_segments_key_returns_empty_map(self):
        """payload 无 segments 键 → 返回空 map，不崩溃。"""
        from transcriber import _build_siliconflow_segment_punct_map
        m = _build_siliconflow_segment_punct_map({})
        assert m == {}

    def test_segments_not_list_returns_empty_map(self):
        """segments 不是 list → 安全降级返回 {}。"""
        from transcriber import _build_siliconflow_segment_punct_map
        m = _build_siliconflow_segment_punct_map({"segments": "invalid"})
        assert m == {}


class TestSiliconflowPunctApplied:
    """验证 _map_siliconflow_to_schema 在 punct_map 下正确追加标点。"""

    def _raw_words(self, texts: list[str]) -> list[dict]:
        return [{"word": t, "start": float(i), "end": float(i) + 0.9} for i, t in enumerate(texts)]

    def test_punct_map_appends_to_correct_word(self):
        """punct_map={1: '。'} → 第 2 个词（索引 1）的 text 追加 '。'"""
        from transcriber import _map_siliconflow_to_schema
        rw = self._raw_words(["你好", "世界"])
        words = _map_siliconflow_to_schema(rw, punct_map={1: "。"})
        assert words[0].text == "你好"
        assert words[1].text == "世界。"

    def test_no_punct_map_unchanged(self):
        """不传 punct_map → 行为与修改前完全一致。"""
        from transcriber import _map_siliconflow_to_schema
        rw = self._raw_words(["词甲", "词乙"])
        words = _map_siliconflow_to_schema(rw)
        assert words[0].text == "词甲"
        assert words[1].text == "词乙"

    def test_timestamps_immutable_with_punct_map(self):
        """架构师红线：punct_map 追加标点，timestamps 完全不变。"""
        from transcriber import _map_siliconflow_to_schema
        rw = [
            {"word": "资产", "start": 1.0, "end": 1.8},
            {"word": "负债", "start": 2.0, "end": 2.9},
        ]
        words = _map_siliconflow_to_schema(rw, punct_map={1: "。"})
        assert words[0].start_time == pytest.approx(1.0)
        assert words[0].end_time == pytest.approx(1.8)
        assert words[1].start_time == pytest.approx(2.0)
        assert words[1].end_time == pytest.approx(2.9)
        assert words[0].word_index == 0
        assert words[1].word_index == 1

    def test_word_index_sequence_intact(self):
        """word_index 依然从 0 连续递增，不受 punct_map 影响。"""
        from transcriber import _map_siliconflow_to_schema
        rw = self._raw_words(["甲", "乙", "丙"])
        words = _map_siliconflow_to_schema(rw, punct_map={2: "！"})
        assert [w.word_index for w in words] == [0, 1, 2]


# ═══════════════════════════════════════════════════════════════════════════
# Part 4: format_transcript_plain_by_speaker 段内句子换行
# ═══════════════════════════════════════════════════════════════════════════

class TestTranscriptFormatterSentenceBreaks:
    """验证格式化函数在同说话人内部遇到句末标点时插入 \\n 换行。"""

    def test_same_speaker_no_punct_no_extra_newline(self):
        """同说话人无标点 → 一行，行为与修改前一致（回归保护）。"""
        from transcriber import format_transcript_plain_by_speaker
        words = [
            _tw(0, "你好", spk="spk_0"),
            _tw(1, "世界", spk="spk_0"),
        ]
        out = format_transcript_plain_by_speaker(words)
        # 只有一个说话人块，内部无 \n
        assert out == "[发言人 1]: 你好世界"

    def test_same_speaker_two_sentences_split_by_newline(self):
        """同说话人，两句末词有句号 → 句间插 \\n（段内换行）。"""
        from transcriber import format_transcript_plain_by_speaker
        words = [
            _tw(0, "今天。", spk="spk_0"),
            _tw(1, "明天。", spk="spk_0"),
        ]
        out = format_transcript_plain_by_speaker(words)
        assert "[发言人 1]:" in out
        # 两句之间有 \n（单换行）
        assert "今天。\n明天。" in out
        # 不应该有 \n\n（那是说话人之间的分隔）
        assert "\n\n" not in out

    def test_two_speakers_separated_by_double_newline(self):
        """两个说话人之间用 \\n\\n 分隔（原有行为保留）。"""
        from transcriber import format_transcript_plain_by_speaker
        words = [
            _tw(0, "提问。", spk="spk_0"),
            _tw(1, "回答。", spk="spk_1"),
        ]
        out = format_transcript_plain_by_speaker(words)
        parts = out.split("\n\n")
        assert len(parts) == 2
        assert parts[0].startswith("[发言人 1]:")
        assert parts[1].startswith("[发言人 2]:")

    def test_real_world_mixed_scenario(self):
        """真实场景：同说话人两句 + 换人一句，验证分段正确。"""
        from transcriber import format_transcript_plain_by_speaker
        words = [
            _tw(0, "我们", spk="spk_0"),
            _tw(1, "今年。", spk="spk_0"),   # 第一句末
            _tw(2, "明年。", spk="spk_0"),   # 第二句末
            _tw(3, "好的。", spk="spk_1"),   # 新说话人
        ]
        out = format_transcript_plain_by_speaker(words)
        speaker_blocks = out.split("\n\n")
        assert len(speaker_blocks) == 2

        # 发言人 1 的块包含两句，用 \n 分隔
        block1 = speaker_blocks[0]
        assert "我们今年。" in block1
        assert "明年。" in block1
        assert "\n" in block1.replace("[发言人 1]: ", "")  # 段内有换行

        # 发言人 2 只有一句
        block2 = speaker_blocks[1]
        assert "好的。" in block2

    def test_alignment_not_affected_by_display_format(self):
        """
        对齐免疫红线：格式化函数只影响展示文字，
        TranscriptionWord 列表本身（用于 Base64 跳转）完全不变。
        """
        from transcriber import format_transcript_plain_by_speaker
        original_words = [
            _tw(0, "数据。", spk="spk_0", t0=0.0, t1=0.5),
            _tw(1, "实证。", spk="spk_0", t0=0.5, t1=1.0),
        ]
        # 调用格式化不修改原始列表
        _ = format_transcript_plain_by_speaker(original_words)
        assert original_words[0].text == "数据。"
        assert original_words[0].start_time == 0.0
        assert original_words[0].end_time == 0.5
        assert original_words[0].word_index == 0
        assert original_words[1].text == "实证。"
        assert original_words[1].start_time == 0.5
        assert original_words[1].end_time == 1.0
        assert original_words[1].word_index == 1

    def test_existing_behavior_two_speakers_unchanged(self):
        """回归保护：双说话人场景的行为与修改前完全一致。"""
        from transcriber import format_transcript_plain_by_speaker
        words = [
            _tw(0, "你好", spk="spk_0", t0=0.0, t1=0.2),
            _tw(1, "世界", spk="spk_0", t0=0.2, t1=0.4),
            _tw(2, "收到", spk="spk_1", t0=0.5, t1=0.7),
            _tw(3, "明白", spk="spk_1", t0=0.7, t1=0.9),
        ]
        out = format_transcript_plain_by_speaker(words)
        assert out.startswith("[发言人 1]: ")
        assert "[发言人 2]: " in out
        parts = out.split("\n\n")
        assert len(parts) == 2
        assert parts[0].startswith("[发言人 1]:")
        assert parts[1].startswith("[发言人 2]:")
        assert "你好世界" in parts[0]
        assert "收到明白" in parts[1]
