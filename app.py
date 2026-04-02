"""
AI 路演与访谈复盘系统 — Streamlit 企业级控制台（按录音逐条归档 + 动态路径）。
发版主线 V7.5（与根目录 build_release.py → CURRENT_VERSION 对齐）。

支持单次 1 个或多个音频：每条录音单独填写被访谈人、备注与参考 QA。
运行：在项目根目录执行  streamlit run app.py
依赖：pip install streamlit（及项目既有 transcriber / llm_judge / report_builder 依赖）
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import uuid

import pandas as pd
from pathlib import Path


def get_resource_path(relative_path):
    """获取资源的绝对路径，无缝兼容 Python 脚本开发环境与 PyInstaller 打包 EXE 环境"""
    if hasattr(sys, "_MEIPASS"):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


_SRC = Path(get_resource_path("src"))
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from draft_manager import list_available_drafts, load_draft, save_draft
from garbage_collector import sweep_stale_intermediate_json
from runtime_paths import get_writable_app_root
from system_debug_log import read_debug_log_bytes, setup_file_logging

_ROOT = Path(get_resource_path(".")).resolve()
_ENV_PATH = get_writable_app_root() / ".env"

import streamlit as st
from dotenv import load_dotenv, set_key
from openai import APIError, OpenAI

load_dotenv(_ENV_PATH)

from audio_filename_hints import guess_batch_fields_from_stem, stem_from_audio_filename
from audio_preprocess import smart_compress_media
from document_reader import extract_text_from_files
from job_pipeline import (
    DEFAULT_HTML_FILENAME_MASKS,
    OTHER_SCENE_KEY,
    PitchFileJobParams,
    SCENE_MAP,
    apply_html_filename_masks,
    build_explicit_context,
    run_pitch_file_job,
    safe_fs_segment,
)
from sensitive_words import parse_sensitive_words
from transcriber import format_transcript_plain_by_speaker, transcribe_audio
from report_builder import (
    HtmlExportOptions,
    apply_asr_original_text_override,
    desensitize_text,
    generate_html_report,
    snippet_audio_mp3_bytes,
)
from schema import AnalysisReport, RiskPoint, TranscriptionWord

_SCENE_SELECT_PLACEHOLDER = "—— 请先选择业务场景 ——"


def _preflight_subprocess_kwargs() -> dict:
    """Windows 下隐藏 FFmpeg 自检子进程控制台。"""
    kw: dict = {}
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        kw["startupinfo"] = si
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


def _probe_ffmpeg_for_ui() -> tuple[bool, str]:
    """V4.8 / V6.2 侧边栏体检：FFmpeg 路径与 -version 探针。返回 (是否就绪, 失败简述)。"""
    try:
        import imageio_ffmpeg
    except ImportError as e:
        return False, f"缺少依赖 imageio-ffmpeg：{e}"

    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        return False, f"get_ffmpeg_exe 失败：{e}"

    if not ffmpeg_exe or not Path(ffmpeg_exe).is_file():
        return False, "未获得有效 FFmpeg 可执行路径"

    try:
        r = subprocess.run(
            [ffmpeg_exe, "-version"],
            capture_output=True,
            timeout=2,
            check=False,
            **_preflight_subprocess_kwargs(),
        )
    except FileNotFoundError:
        return False, "无法启动 FFmpeg 进程（FileNotFoundError）"
    except subprocess.TimeoutExpired:
        return False, "FFmpeg -version 超时（>2s）"

    if r.returncode != 0:
        tail = (r.stderr or b"")[:200].decode("utf-8", errors="replace")
        return False, tail or f"返回码 {r.returncode}"
    return True, ""


def _v3_clear_review_session_state() -> None:
    """新一轮生成前清空审查台相关状态，避免旧 widget 与草稿串台。"""
    for _k in list(st.session_state.keys()):
        if not isinstance(_k, str):
            continue
        if _k.startswith(
            ("v3", "report_draft_", "words_", "v3_ctx_", "v46_")
        ):
            del st.session_state[_k]
    st.session_state["v3_review_stems"] = []


def _v3_ensure_rid(rp: dict) -> str:
    rid = rp.get("_rid")
    if not rid:
        rid = uuid.uuid4().hex[:16]
        rp["_rid"] = rid
    return str(rid)


def _v3_init_header_widgets(stem: str, draft: dict) -> None:
    sa = draft.get("scene_analysis") or {}
    if f"v3_{stem}_scene_type" not in st.session_state:
        st.session_state[f"v3_{stem}_scene_type"] = sa.get("scene_type", "")
    if f"v3_{stem}_speaker_roles" not in st.session_state:
        st.session_state[f"v3_{stem}_speaker_roles"] = sa.get("speaker_roles", "")
    if f"v3_{stem}_total_score" not in st.session_state:
        st.session_state[f"v3_{stem}_total_score"] = int(draft.get("total_score", 0))
    if f"v3_{stem}_total_ded" not in st.session_state:
        st.session_state[f"v3_{stem}_total_ded"] = draft.get(
            "total_score_deduction_reason", ""
        )


def _v3_init_risk_widgets(stem: str, draft: dict) -> None:
    for rp in draft.get("risk_points") or []:
        rid = _v3_ensure_rid(rp)
        base = f"v3rp_{stem}_{rid}"
        if f"{base}_lvl" not in st.session_state:
            st.session_state[f"{base}_lvl"] = rp.get("risk_level", "一般")
        if f"{base}_t1" not in st.session_state:
            st.session_state[f"{base}_t1"] = rp.get("tier1_general_critique", "")
        if f"{base}_t2" not in st.session_state:
            st.session_state[f"{base}_t2"] = rp.get("tier2_qa_alignment", "")
        if f"{base}_im" not in st.session_state:
            st.session_state[f"{base}_im"] = rp.get("improvement_suggestion", "")
        if f"{base}_ded" not in st.session_state:
            st.session_state[f"{base}_ded"] = rp.get("deduction_reason", "")
        if f"{base}_ort" not in st.session_state:
            st.session_state[f"{base}_ort"] = rp.get("original_text", "")


def _v3_snapshot_report_for_draft(stem: str) -> dict:
    """将审查台控件值合并为可恢复的 report 字典，并保留各 risk_point 的 _rid。"""
    draft = st.session_state.get(f"report_draft_{stem}")
    if not draft:
        return {}
    built = _v3_build_report_dict_from_widgets(stem)
    old_rps = draft.get("risk_points") or []
    new_rps = built.get("risk_points") or []
    for i, nr in enumerate(new_rps):
        rid = old_rps[i].get("_rid") if i < len(old_rps) else None
        if rid:
            nr["_rid"] = rid
        else:
            nr.setdefault("_rid", uuid.uuid4().hex[:16])
    built["risk_points"] = new_rps
    return built


def _v3_build_report_dict_from_widgets(stem: str) -> dict:
    draft = st.session_state[f"report_draft_{stem}"]
    rps_out: list[dict] = []
    for rp in draft.get("risk_points") or []:
        rid = rp.get("_rid")
        if not rid:
            continue
        base = f"v3rp_{stem}_{rid}"
        rps_out.append(
            {
                "risk_level": st.session_state.get(f"{base}_lvl", rp.get("risk_level", "一般")),
                "tier1_general_critique": st.session_state.get(
                    f"{base}_t1", rp.get("tier1_general_critique", "")
                ),
                "tier2_qa_alignment": st.session_state.get(
                    f"{base}_t2", rp.get("tier2_qa_alignment", "")
                ),
                "improvement_suggestion": st.session_state.get(
                    f"{base}_im", rp.get("improvement_suggestion", "")
                ),
                "start_word_index": int(rp.get("start_word_index", 0)),
                "end_word_index": int(rp.get("end_word_index", 0)),
                "deduction_reason": st.session_state.get(
                    f"{base}_ded", rp.get("deduction_reason", "")
                ),
                "original_text": st.session_state.get(
                    f"{base}_ort", rp.get("original_text", "")
                ),
                "score_deduction": int(rp.get("score_deduction", 0) or 0),
                "is_manual_entry": bool(rp.get("is_manual_entry", False)),
            }
        )
    ts = st.session_state.get(f"v3_{stem}_total_score", draft.get("total_score", 0))
    try:
        ts_int = int(ts)
    except (TypeError, ValueError):
        ts_int = int(draft.get("total_score", 0))
    ts_int = max(0, min(100, ts_int))
    return {
        "scene_analysis": {
            "scene_type": st.session_state.get(
                f"v3_{stem}_scene_type",
                (draft.get("scene_analysis") or {}).get("scene_type", ""),
            ),
            "speaker_roles": st.session_state.get(
                f"v3_{stem}_speaker_roles",
                (draft.get("scene_analysis") or {}).get("speaker_roles", ""),
            ),
        },
        "total_score": ts_int,
        "total_score_deduction_reason": st.session_state.get(
            f"v3_{stem}_total_ded", draft.get("total_score_deduction_reason", "")
        ),
        "risk_points": rps_out,
    }


def _v3_finalize_stem(stem: str) -> Path:
    ctx = st.session_state[f"v3_ctx_{stem}"]
    # 深拷贝后再校验；JSON 正文保持审查台明文，仅外发 HTML 文件名做 DLP 脱敏
    payload = copy.deepcopy(_v3_build_report_dict_from_widgets(stem))
    report = AnalysisReport.model_validate(payload)
    words = [
        TranscriptionWord.model_validate(x) for x in st.session_state[f"words_{stem}"]
    ]
    report_for_disk = apply_asr_original_text_override(report, words)
    Path(ctx["analysis_json"]).write_text(
        json.dumps(report_for_disk.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    proj = (ctx.get("project_name") or "").strip() or "项目"
    iv = (ctx.get("interviewee") or "").strip() or "发言人"
    proj_d = desensitize_text(proj, is_person=False)
    iv_d = desensitize_text(iv, is_person=True)
    stem_part = safe_fs_segment(Path(ctx["audio_path"]).stem)
    html_name = (
        f"{safe_fs_segment(proj_d)}-{safe_fs_segment(iv_d)}_{stem_part}_复盘报告.html"
    )
    html_path = Path(ctx["audio_path"]).parent / html_name

    hopt = HtmlExportOptions(
        footer_watermark=ctx.get("watermark") or "",
        content_replace_map=ctx.get("html_mask_map") if ctx.get("mask_html_body") else None,
        show_generated_timestamp=True,
    )
    generate_html_report(
        Path(ctx["audio_path"]),
        words,
        report_for_disk,
        html_path,
        export_options=hopt,
    )
    final = html_path.resolve()
    st.session_state[f"v46_preview_html_{stem}"] = str(final)
    return final


def _v3_render_single_stem_review(stem: str) -> None:
    draft = st.session_state.get(f"report_draft_{stem}")
    if not draft:
        st.warning("未找到该条的审查草稿。")
        return
    _v3_init_header_widgets(stem, draft)
    _v3_init_risk_widgets(stem, draft)

    st.markdown(f"**录音主文件名：** `{stem}`")

    st.text_area(
        "场景推断（可编辑）",
        key=f"v3_{stem}_scene_type",
        height=68,
    )
    st.text_area(
        "身份与氛围（可编辑）",
        key=f"v3_{stem}_speaker_roles",
        height=68,
    )
    c1, c2 = st.columns(2)
    with c1:
        st.number_input(
            "综合得分（0–100）",
            min_value=0,
            max_value=100,
            key=f"v3_{stem}_total_score",
        )
    with c2:
        st.caption("总分扣分说明见下方文本框。")
    st.text_area(
        "总分扣分说明 / 与 QA 对照",
        key=f"v3_{stem}_total_ded",
        height=100,
    )

    words_raw = st.session_state.get(f"words_{stem}") or []
    words_models = [TranscriptionWord.model_validate(x) for x in words_raw]
    audio_fs_path = Path(st.session_state[f"v3_ctx_{stem}"]["audio_path"])

    st.subheader("翻车片段与逐字稿")
    rps = st.session_state[f"report_draft_{stem}"].get("risk_points") or []

    for idx, rp in enumerate(list(rps)):
        rid = _v3_ensure_rid(rp)
        is_manual = bool(rp.get("is_manual_entry", False))
        with st.expander(
            f"片段 #{idx + 1} · {'人工增补' if is_manual else 'AI 提取'} · {rid}",
            expanded=False,
        ):
            st.selectbox(
                "严重程度",
                options=["严重", "一般", "轻微"],
                key=f"v3rp_{stem}_{rid}_lvl",
            )
            st.text_area(
                "Tier 1 · 顶尖视角",
                key=f"v3rp_{stem}_{rid}_t1",
                height=100,
            )
            st.text_area(
                "Tier 2 · QA 对齐",
                key=f"v3rp_{stem}_{rid}_t2",
                height=100,
            )
            st.text_area(
                "改进建议",
                key=f"v3rp_{stem}_{rid}_im",
                height=80,
            )
            st.text_area(
                "扣分原因 / QA 口径偏离说明",
                key=f"v3rp_{stem}_{rid}_ded",
                height=80,
            )
            st.text_area(
                "🎙️ 发言人口述实录",
                key=f"v3rp_{stem}_{rid}_ort",
                height=100,
                help="模型洗稿后的口述实录，可编辑；将写入 HTML「发言人口述实录」区块。",
            )

            if not is_manual and audio_fs_path.is_file():
                sw, ew = int(rp.get("start_word_index", 0)), int(rp.get("end_word_index", 0))
                blob = snippet_audio_mp3_bytes(audio_fs_path, words_models, sw, ew)
                if blob:
                    st.audio(io.BytesIO(blob), format="audio/mpeg")
                else:
                    st.caption("（无法生成该片段试听，请检查词索引）")
            elif is_manual:
                st.caption("人工条目无词级切片与自动试听。")

            if st.button(
                "🗑️ 删除此片段",
                key=f"v3del_{stem}_{rid}",
            ):
                st.session_state[f"report_draft_{stem}"]["risk_points"] = [
                    x
                    for x in st.session_state[f"report_draft_{stem}"]["risk_points"]
                    if x.get("_rid") != rid
                ]
                st.rerun()

    with st.expander("➕ 添加人工发现的复盘点", expanded=False):
        st.text_input("标题 / Tier1 要点", key=f"v3man_{stem}_t1")
        st.text_area("问题描述 / Tier2", key=f"v3man_{stem}_t2", height=80)
        st.text_area("改进建议", key=f"v3man_{stem}_im", height=80)
        if st.button("保存到本条录音审查单", key=f"v3man_{stem}_save"):
            nt = (st.session_state.get(f"v3man_{stem}_t1") or "").strip()
            if not nt:
                st.error("请至少填写标题/Tier1 要点。")
            else:
                entry = {
                    "risk_level": "轻微",
                    "tier1_general_critique": nt,
                    "tier2_qa_alignment": st.session_state.get(f"v3man_{stem}_t2", ""),
                    "improvement_suggestion": st.session_state.get(f"v3man_{stem}_im", ""),
                    "original_text": "",
                    "start_word_index": 0,
                    "end_word_index": 0,
                    "score_deduction": 0,
                    "deduction_reason": "人工录入",
                    "is_manual_entry": True,
                    "_rid": uuid.uuid4().hex[:16],
                }
                st.session_state[f"report_draft_{stem}"]["risk_points"].append(entry)
                st.rerun()

    if st.button(
        "✅ 确认无误，锁定并生成最终版 HTML 报告",
        type="primary",
        key=f"v3finalize_{stem}",
    ):
        try:
            final_html = _v3_finalize_stem(stem)
            st.success(
                f"已锁定：**{stem}** → JSON 与 HTML 已写入归档目录。\n"
                f"HTML（脱敏文件名）：`{final_html.name}`"
            )
        except Exception as ex:
            st.error(f"导出失败：{ex!s}")

    ph = st.session_state.get(f"v46_preview_html_{stem}")
    if ph and os.name == "nt" and Path(ph).is_file():
        c_open1, c_open2 = st.columns(2)
        with c_open1:
            if st.button("📂 打开报告所在文件夹", key=f"v46dir_{stem}"):
                os.startfile(str(Path(ph).parent))
        with c_open2:
            if st.button("🌐 立即预览报告", key=f"v46open_{stem}"):
                os.startfile(ph)


def _v7_collect_draft_payload() -> dict:
    """从当前 session_state 收集审查台快照，供本地草稿箱落盘。"""
    stems: list[str] = st.session_state.get("v3_review_stems") or []
    blob: dict = {
        "version": 7,
        "session_id": str(st.session_state.get("session_id") or ""),
        "v3_review_stems": list(stems),
        "reports": {},
        "words": {},
        "ctx": {},
    }
    for stem in stems:
        try:
            blob["reports"][stem] = _v3_snapshot_report_for_draft(stem)
        except Exception:
            blob["reports"][stem] = copy.deepcopy(
                st.session_state.get(f"report_draft_{stem}") or {}
            )
        blob["words"][stem] = copy.deepcopy(st.session_state.get(f"words_{stem}") or [])
        blob["ctx"][stem] = copy.deepcopy(st.session_state.get(f"v3_ctx_{stem}") or {})
    return blob


def _v7_apply_draft_payload(data: dict) -> None:
    stems = data.get("v3_review_stems") or []
    reports = data.get("reports") or {}
    words = data.get("words") or {}
    ctx = data.get("ctx") or {}
    for stem in stems:
        if stem in reports:
            st.session_state[f"report_draft_{stem}"] = copy.deepcopy(reports[stem])
        if stem in words:
            st.session_state[f"words_{stem}"] = copy.deepcopy(words[stem])
        if stem in ctx:
            st.session_state[f"v3_ctx_{stem}"] = copy.deepcopy(ctx[stem])
    st.session_state["v3_review_stems"] = list(stems)
    sid = data.get("session_id")
    if sid:
        st.session_state["session_id"] = str(sid)


def _v7_latest_draft_session_id() -> str | None:
    """在可用草稿中选最近修改的一个 session_id。"""
    ids = list_available_drafts()
    best: str | None = None
    best_t = -1.0
    root = get_writable_app_root() / ".drafts"
    for sid in ids:
        p = root / f"draft_{sid}.json"
        try:
            t = p.stat().st_mtime
        except OSError:
            continue
        if t > best_t:
            best_t = t
            best = sid
    return best


def _v3_render_review_workbench() -> None:
    stems: list[str] = st.session_state.get("v3_review_stems") or []
    if not stems:
        return
    sid = str(st.session_state.get("session_id") or "").strip()
    if sid:
        try:
            save_draft(sid, _v7_collect_draft_payload())
        except Exception:
            logging.getLogger("ai_pitch_coach.ui").exception("静默保存草稿失败")
    st.divider()
    st.subheader("🔍 报告审查与人工编辑台（V3.0）")
    st.caption("✅ 数据已自动静默保存至本地草稿箱")
    st.caption(
        "以下为 AI 初稿；请逐条核对、编辑或删除片段，并可人工增补。"
        "仅当点击「锁定并生成最终版 HTML」后才会写入最终 HTML。"
    )
    if len(stems) == 1:
        _v3_render_single_stem_review(stems[0])
    else:
        tabs = st.tabs(stems)
        for tab, stem in zip(tabs, stems):
            with tab:
                _v3_render_single_stem_review(stem)


def _normalize_sniper_editor_df(df):
    """兼容 V7.5 前列名「人工疑点」→「找茬疑点」，避免会话里旧表丢列。"""
    if df is None or not hasattr(df, "columns"):
        return df
    if "人工疑点" in df.columns and "找茬疑点" not in df.columns:
        return df.rename(columns={"人工疑点": "找茬疑点"})
    return df


def _batch_sniper_targets_json(idx: int) -> str:
    """从狙击表读取数据，序列化为 JSON（quote/reason）。
    优先读 widget 托管的用户编辑结果（ed_key），兜底读初始数据（init_key）。
    """
    ed_key = f"batch_sniper_editor_{idx}"
    init_key = f"batch_sniper_init_{idx}"
    df = st.session_state.get(ed_key) or st.session_state.get(init_key)
    df = _normalize_sniper_editor_df(df)
    if df is None:
        return "[]"
    if not hasattr(df, "iterrows"):
        return "[]"
    rows_out: list[dict[str, str]] = []
    for _, row in df.iterrows():
        q = str(row.get("原文引用", "") or "").strip()
        r = str(row.get("找茬疑点", row.get("人工疑点", "")) or "").strip()
        if q or r:
            rows_out.append({"quote": q, "reason": r})
    return json.dumps(rows_out, ensure_ascii=False)


def _v71_transcribe_upload_to_plain(uf) -> str:
    """仅转写上传文件为可读纯文本，并将结果存入 ASR 内存缓存。
    缓存命中时直接返回，跳过云端调用；点击「生成报告」时主流程可复用同一缓存。
    """
    raw = uf.getvalue()
    file_hash = _file_md5(raw)
    asr_cache: dict = st.session_state.setdefault("asr_cache", {})
    if file_hash in asr_cache:
        return asr_cache[file_hash]["plain"]

    suffix = Path(uf.name).suffix or ".wav"
    f1 = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    f1.write(raw)
    f1.close()
    paths: list[Path] = [Path(f1.name)]
    work = paths[0]
    try:
        if len(raw) >= 10 * 1024 * 1024:
            cres = smart_compress_media(raw, filename_hint=uf.name)
            if cres.did_compress:
                f2 = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
                f2.write(cres.data)
                f2.close()
                p2 = Path(f2.name)
                paths.append(p2)
                try:
                    work.unlink(missing_ok=True)
                except OSError:
                    pass
                paths.remove(work)
                work = p2
        words = transcribe_audio(work, out_json_path=None)
        plain = format_transcript_plain_by_speaker(words)
        asr_cache[file_hash] = {
            "words": [w.model_dump() for w in words],
            "plain": plain,
        }
        return plain
    finally:
        for p in paths:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


def _parse_filename_mask_lines(raw: str) -> dict[str, str]:
    """解析侧边栏「每行：原名⇒代号」或 原名=>代号 或 原名=代号。"""
    out: dict[str, str] = {}
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        for sep in ("⇒", "=>", "->", "="):
            if sep in s:
                a, b = s.split(sep, 1)
                k, v = a.strip(), b.strip()
                if k and v:
                    out[k] = v
                break
    return out


def _merge_html_filename_masks(sidebar_text: str) -> dict[str, str]:
    merged = dict(DEFAULT_HTML_FILENAME_MASKS)
    merged.update(_parse_filename_mask_lines(sidebar_text))
    return merged


def _env_configured(key: str) -> bool:
    v = os.getenv(key)
    return bool(v and str(v).strip())


def _qa_uploader_key_suffix(audio_name: str) -> str:
    """稳定短后缀，避免特殊字符进入 Streamlit widget key。"""
    return hashlib.sha256((audio_name or "").encode("utf-8")).hexdigest()[:12]


def _file_md5(data: bytes) -> str:
    """计算文件内容 MD5，用作 ASR 内存缓存键（非安全场景，仅做内容去重）。"""
    return hashlib.md5(data).hexdigest()  # noqa: S324


def _as_upload_list(x: object) -> list:
    if x is None:
        return []
    if isinstance(x, (list, tuple)):
        return list(x)
    return [x]


def _ping_dashscope_compatible(api_key: str) -> tuple[bool, str]:
    """阿里云百炼 DashScope OpenAI 兼容接口极简问候（max_tokens=5）。"""
    key = (api_key or "").strip()
    if not key:
        return False, "Key 为空"
    try:
        client = OpenAI(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key=key,
        )
        r = client.chat.completions.create(
            model="qwen-turbo",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
            temperature=0,
        )
        if r.choices:
            return True, ""
        return False, "响应无 choices"
    except APIError as e:
        return False, f"APIError: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _ping_deepseek(api_key: str) -> tuple[bool, str]:
    """DeepSeek 官方 OpenAI 兼容接口极简问候（max_tokens=5）。"""
    key = (api_key or "").strip()
    if not key:
        return False, "Key 为空"
    try:
        client = OpenAI(
            base_url="https://api.deepseek.com",
            api_key=key,
        )
        r = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
            temperature=0,
        )
        if r.choices:
            return True, ""
        return False, "响应无 choices"
    except APIError as e:
        return False, f"APIError: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main() -> None:
    st.set_page_config(
        page_title="AI 路演与访谈复盘系统",
        page_icon="🚀",
        layout="wide",
    )
    setup_file_logging()
    logging.getLogger("ai_pitch_coach").debug("Streamlit main() rerun")

    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())

    with st.sidebar:
        latest_draft_sid = _v7_latest_draft_session_id()
        if latest_draft_sid and not st.session_state.get("v3_review_stems"):
            st.info("检测到本地有未完成的审查草稿，可从下方恢复。")
            if st.button("恢复上次未完成的审查草稿", key="v7_restore_draft_btn"):
                loaded = load_draft(latest_draft_sid)
                if loaded:
                    _v7_apply_draft_payload(loaded)
                    st.rerun()
                else:
                    st.error("草稿已损坏或不存在，无法恢复。")
        st.header("⚙️ 系统配置")
        workspace_default = str(get_writable_app_root())
        workspace = st.text_input(
            "数据归档根目录 (Workspace Root)",
            value=workspace_default,
            help="可填企业共享盘路径；留空则使用当前项目根目录。",
        ).strip()

        if "dash_key_field" not in st.session_state:
            st.session_state.dash_key_field = os.getenv("DASHSCOPE_API_KEY") or ""
        if "deep_key_field" not in st.session_state:
            st.session_state.deep_key_field = os.getenv("DEEPSEEK_API_KEY") or ""

        with st.expander("🔑 首次使用请配置 API 密钥", expanded=True):
            st.text_input(
                "阿里云 API Key (用于转写)",
                type="password",
                key="dash_key_field",
                help="写入环境变量 DASHSCOPE_API_KEY（百炼 DashScope，录音转写等）。",
            )
            st.text_input(
                "DeepSeek API Key (用于毒舌分析)",
                type="password",
                key="deep_key_field",
                help="写入环境变量 DEEPSEEK_API_KEY（默认打分分析）。",
            )
            if st.button("💾 保存并测试连接", key="save_test_api_keys"):
                ds = (st.session_state.dash_key_field or "").strip()
                dk = (st.session_state.deep_key_field or "").strip()
                if not ds or not dk:
                    st.session_state.env_all_ok = False
                    st.error("❌ 请先完整填写两个 API Key。")
                else:
                    try:
                        set_key(str(_ENV_PATH), "DASHSCOPE_API_KEY", ds)
                        set_key(str(_ENV_PATH), "DEEPSEEK_API_KEY", dk)
                        load_dotenv(_ENV_PATH, override=True)
                        os.environ["DASHSCOPE_API_KEY"] = ds
                        os.environ["DEEPSEEK_API_KEY"] = dk
                        with st.status("🔌 正在全量环境自检...", expanded=True) as status:
                            status.update(
                                label="正在测试阿里云 DashScope（大模型兼容接口）...",
                                state="running",
                            )
                            ok_a, err_a = _ping_dashscope_compatible(ds)
                            if ok_a:
                                status.write("✅ 阿里云 DashScope：绿灯")
                            else:
                                status.write(f"❌ 阿里云 DashScope：{err_a}")
                            status.update(
                                label="正在测试 DeepSeek...",
                                state="running",
                            )
                            ok_b, err_b = _ping_deepseek(dk)
                            if ok_b:
                                status.write("✅ DeepSeek：绿灯")
                            else:
                                status.write(f"❌ DeepSeek：{err_b}")
                            status.update(
                                label="正在检测本地视听引擎 (FFmpeg)...",
                                state="running",
                            )
                            ok_ff, err_ff = _probe_ffmpeg_for_ui()
                            if ok_ff:
                                status.write("✅ 视听引擎 (FFmpeg)：已就绪")
                            else:
                                status.markdown(
                                    ":red[❌ 视听引擎 (FFmpeg)：未找到或被拦截]"
                                )
                            status.update(label="自检完成", state="complete")
                        if ok_a and ok_b and ok_ff:
                            st.session_state.env_all_ok = True
                            st.success(
                                "✅ 环境全绿！API与本地视听引擎均已完美就绪。"
                            )
                        else:
                            st.session_state.env_all_ok = False
                            parts: list[str] = []
                            if not ok_a:
                                parts.append(f"阿里云：{err_a}")
                            if not ok_b:
                                parts.append(f"DeepSeek：{err_b}")
                            if not ok_ff:
                                parts.append(f"FFmpeg：{err_ff}")
                            st.error("❌ 环境自检未通过：" + "；".join(parts))
                    except Exception as e:
                        st.session_state.env_all_ok = False
                        st.error(f"❌ 保存或测试失败：{e!s}")

        if st.session_state.get("env_all_ok"):
            st.success("✅ 环境：API + FFmpeg 已验证")
        else:
            st.caption(
                "⚠️ 须点击「保存并测试连接」直至阿里云、DeepSeek、FFmpeg 全部通过后方可生成报告。"
            )

        if "sensitive_words_raw" not in st.session_state:
            st.session_state.sensitive_words_raw = "福创投, 迪策, 净利润"
        st.text_area(
            "🔒 保密词汇黑名单（支持换行、空格、中英文逗号/分号混用）",
            key="sensitive_words_raw",
            height=88,
            help="在调用大模型前，对转写词文本替换为 ***。可点击「识别保密词汇」预览系统解析结果。",
        )
        if st.button("🔍 识别保密词汇", key="btn_parse_sensitive_words"):
            parsed = parse_sensitive_words(
                str(st.session_state.get("sensitive_words_raw") or "")
            )
            st.session_state.sensitive_words_last_parsed = parsed
        last_p = st.session_state.get("sensitive_words_last_parsed")
        if last_p is not None:
            if last_p:
                show_n = 24
                if len(last_p) <= show_n:
                    joined = "，".join(last_p)
                    st.caption(
                        f"已成功识别并提取 {len(last_p)} 个保密词汇：{joined}"
                    )
                else:
                    joined = "，".join(last_p[:show_n])
                    st.caption(
                        f"已成功识别并提取 {len(last_p)} 个保密词汇：{joined}…"
                        f"（此处仅展示前 {show_n} 个）"
                    )
            else:
                st.caption("已成功识别：当前列表为空（未提取到任何非空词汇）。")

        filename_mask_input = st.text_area(
            "📤 外发 HTML 文件名脱敏（每行：原名⇒代号）",
            value="",
            height=100,
            help=(
                "仅改变「复盘报告.html」的文件名，便于外发；内置已含 迪策资本⇒DC资本、邓勇⇒DY。"
                "可追加一行一条，支持 ⇒、=>、= 分隔。"
            ),
        )
        mask_html_body = st.checkbox(
            "HTML 正文同步脱敏（场景与翻车卡片文案按上表替换）",
            value=False,
            help=(
                "开启后，仅影响生成的 .html 展示；同目录 *_analysis_report.json 仍为完整原文，便于内部分析。"
                "外发 HTML 时建议勾选。"
            ),
        )
        html_watermark = st.text_input(
            "HTML 页脚水印（可选）",
            value="",
            placeholder="例如：仅供内部评审，禁止外传",
            help="显示在报告页脚醒目位置，便于外发合规提示。",
        )

        st.subheader("密钥环境变量")
        st.caption(
            f"{'✅' if _env_configured('DASHSCOPE_API_KEY') else '❌'} DASHSCOPE_API_KEY（阿里云）"
        )
        st.caption(
            f"{'✅' if _env_configured('DEEPSEEK_API_KEY') else '❌'} DEEPSEEK_API_KEY（DeepSeek）"
        )

    if not st.session_state.get("v4_startup_gc_started"):
        st.session_state["v4_startup_gc_started"] = True
        ws_gc = (workspace or "").strip() or str(get_writable_app_root())

        def _startup_gc() -> None:
            try:
                n = sweep_stale_intermediate_json(ws_gc)
                if n:
                    logging.getLogger("garbage_collector").info(
                        "启动静默 GC：已删除 %d 个过期中间 JSON（根：%s）",
                        n,
                        ws_gc,
                    )
            except Exception:
                logging.getLogger("garbage_collector").exception("启动 GC 失败")

        threading.Thread(target=_startup_gc, daemon=True).start()

    st.title("🚀 AI 路演与访谈复盘系统")

    if not st.session_state.get("env_all_ok", False):
        st.warning(
            "⚠️ 请先在左侧侧边栏「🔑 首次使用请配置 API 密钥」中填写 Key，"
            "点击「💾 保存并测试连接」直至阿里云、DeepSeek 与 FFmpeg 全绿后，方可开始生成报告。"
        )

    scene_options = [_SCENE_SELECT_PLACEHOLDER] + list(SCENE_MAP.keys())
    col1, col2 = st.columns(2)
    with col1:
        category = st.selectbox(
            "业务大类（必选）",
            options=scene_options,
            index=0,
            help="请先明确业务场景后再生成，避免 AI 用错复盘视角。",
        )
    with col2:
        batch_name = st.text_input(
            "项目/批次名称（必填）",
            placeholder="例如：某机构代号、尽调批次",
            help="将作为子文件夹名称的一部分，并进入 AI 上下文。",
        )

    st.caption(
        "上传音频（可 1 条或多条）后，在下方按 **每一条录音** 填写被访谈人、备注，并可选上传该段对应的参考 QA。"
    )

    if category == OTHER_SCENE_KEY:
        st.text_input(
            "请填写具体双方身份（必填）",
            placeholder="例如：供应商质量负责人 vs 买方投资机构",
            key="custom_roles_other",
        )

    tab_qa_file, tab_qa_dir = st.tabs(["参考 QA 说明", "选择参考文件夹"])
    with tab_qa_file:
        st.caption(
            "参考 QA 在下方 **「逐录音填写」** 中按文件上传；仅作用于对应那一条录音。"
            "可多选多个 QA 文件，会先合并再截断前 30000 字。支持 txt、md、pdf、docx、xlsx（PPT 请先另存为 PDF）。"
        )
    with tab_qa_dir:
        st.info("预留：后续支持选择本地参考文件夹批量导入。")
        st.text_input(
            "参考文件夹路径（预留，暂未启用）",
            disabled=True,
            key="qa_folder_placeholder",
        )

    uploaded = st.file_uploader(
        "上传音频（可多选）",
        type=["m4a", "mp3", "wav", "mp4", "mpeg", "mpga", "webm"],
        accept_multiple_files=True,
    )

    uploaded_list: list = []
    if uploaded is not None:
        uploaded_list = list(uploaded) if isinstance(uploaded, (list, tuple)) else [uploaded]

    batch_qa_files_per_index: list[list] = []
    if uploaded_list:
        st.subheader("逐录音填写（进入 AI 上下文）")
        st.checkbox(
            "根据录音文件名自动填写被访谈人与狙击表首行疑点",
            value=True,
            key="batch_autofill_filename",
            help=(
                "按常见命名「机构-姓名」与可选末尾 8 位日期解析；更换本行对应录音文件名后会按新文件名重新覆盖被访谈人，"
                "并将解析到的备注写入该条「找茬疑点」列首行。关闭后仅手动填写。"
            ),
        )
        st.caption(
            "调整音频顺序或增删文件后，请逐条核对被访谈人、狙击清单与 QA 是否与录音一致。"
        )
        for idx, uf in enumerate(uploaded_list):
            stem = stem_from_audio_filename(uf.name)
            track_key = f"_batch_audio_stem_{idx}"
            init_key = f"batch_sniper_init_{idx}"   # 初始数据专用，写操作唯一入口
            ed_key = f"batch_sniper_editor_{idx}"   # 仅绑定 data_editor widget，严禁写入
            if init_key not in st.session_state:
                st.session_state[init_key] = pd.DataFrame(
                    [{"原文引用": "", "找茬疑点": ""}]
                )
            st.session_state[init_key] = _normalize_sniper_editor_df(
                st.session_state[init_key]
            )
            if st.session_state.get("batch_autofill_filename", True):
                if st.session_state.get(track_key) != stem:
                    st.session_state[track_key] = stem
                    iv_guess, note_guess = guess_batch_fields_from_stem(stem)
                    st.session_state[f"batch_iv_{idx}"] = iv_guess
                    st.session_state[init_key] = pd.DataFrame(
                        [{"原文引用": "", "找茬疑点": note_guess}]
                    )
                    # 新文件检测到：清除 widget 托管状态，强制下次渲染以 init_key 重新初始化
                    if ed_key in st.session_state:
                        del st.session_state[ed_key]

            st.markdown(f"**文件 {idx + 1}：** `{uf.name}`")
            st.text_input(
                "被访谈人（必填）",
                key=f"batch_iv_{idx}",
                placeholder="本段录音对应的对象",
                help="仅作用于当前这一条录音的打分与复盘。",
            )
            st.caption(
                "🎯 **结构化狙击清单**（`key` 绑定会话，勿把返回值写回 state，避免循环刷新丢数）："
                "「原文引用」贴原话，「找茬疑点」写找茬方向；可多行。"
            )
            # 安全红线（铁律三）：init_key 提供初始数据，ed_key 绑定 widget；严禁反向赋值
            st.data_editor(
                st.session_state[init_key],
                column_config={
                    "原文引用": st.column_config.TextColumn("原文引用", width="large"),
                    "找茬疑点": st.column_config.TextColumn("找茬疑点", width="large"),
                },
                num_rows="dynamic",
                key=ed_key,
                hide_index=True,
            )
            suf = _qa_uploader_key_suffix(uf.name)
            qf = st.file_uploader(
                "本段参考 QA（可选，可多选）",
                type=["txt", "md", "pdf", "docx", "xlsx"],
                accept_multiple_files=True,
                key=f"batch_qa_{idx}_{suf}",
                help=(
                    f"仅用于本条录音「{uf.name}」。不上传仍会生成报告，但对齐深度可能下降。"
                    "多文件会先合并再截断前 30000 字。"
                ),
            )
            batch_qa_files_per_index.append(_as_upload_list(qf))

    st.info(
        "💡 **建议**：尽量为每条录音上传 **对应该方向的 QA** 或口径材料；未上传时仍会生成报告。"
    )

    if "v71_plain_body" not in st.session_state:
        st.session_state["v71_plain_body"] = ""

    if st.button(
        "📄 仅提取文字稿 (提取后可复制原话进行精准核实)",
        key="v71_btn_extract_transcript",
    ):
        if not st.session_state.get("env_all_ok"):
            st.error("请先在左侧侧栏完成 API 与 FFmpeg 全绿自检后再提取文字稿。")
        elif not uploaded_list:
            st.error("请先上传至少一个音频文件。")
        else:
            uf0 = uploaded_list[0]
            if len(uploaded_list) > 1:
                st.caption(
                    f"当前对上传列表中的 **第 1 条** 执行转写：`{uf0.name}`（共 {len(uploaded_list)} 个文件）。"
                )
            try:
                with st.spinner("正在转写，请稍候…"):
                    plain = _v71_transcribe_upload_to_plain(uf0)
                st.session_state["v71_plain_body"] = plain
                st.success(f"已提取约 {len(plain)} 字，可复制到上方对应录音的「原文引用」列。")
            except Exception as ex:
                logging.getLogger("ai_pitch_coach.ui").exception("仅提取文字稿失败")
                st.error(f"转写失败：{ex!s}")

    st.text_area(
        "提取的文字稿（可复制到上方逐录音「原文引用」列）",
        key="v71_plain_body",
        height=280,
        help="先点击上方按钮；按说话人分段的文字可粘贴到狙击清单「原文引用」列以降低切片错位。",
    )

    run = st.button(
        "开始生成复盘报告",
        type="primary",
        disabled=not st.session_state.get("env_all_ok", False),
    )

    if not run:
        st.info("配置侧边栏与业务场景，上传音频后点击按钮开始。")
        if st.session_state.get("v3_review_stems"):
            st.divider()
            _v3_render_review_workbench()
        return

    if not uploaded_list:
        st.warning("请先上传至少一个音频文件。")
        return

    if category == _SCENE_SELECT_PLACEHOLDER:
        st.error("请先在下拉框中选择真实的「业务大类」，不能保留「请先选择业务场景」。")
        return

    if not (batch_name or "").strip():
        st.error("请填写「项目/批次名称」（必填）。")
        return

    for idx in range(len(uploaded_list)):
        iv = (st.session_state.get(f"batch_iv_{idx}") or "").strip()
        if not iv:
            st.error(f"请为录音「{uploaded_list[idx].name}」填写被访谈人（必填）。")
            return

    if category == OTHER_SCENE_KEY:
        cr = (st.session_state.get("custom_roles_other") or "").strip()
        if not cr:
            st.error('业务大类为「05_其他」时，必须填写「具体双方身份」。')
            return

    try:
        sensitive_words = parse_sensitive_words(
            str(st.session_state.get("sensitive_words_raw") or "")
        )
        html_mask_map = _merge_html_filename_masks(filename_mask_input)
        project_name = (batch_name or "").strip()

        batch_qa_texts: list[str] = []
        for idx, uf in enumerate(uploaded_list):
            if idx >= len(batch_qa_files_per_index):
                st.error("内部状态异常：请刷新页面后重新上传音频。")
                return
            per_files = batch_qa_files_per_index[idx]
            batch_qa_texts.append(
                extract_text_from_files(per_files, max_chars=30000)
            )

        root_path = Path(workspace).expanduser() if workspace else get_writable_app_root()
        try:
            root_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            st.error(f"无法创建或使用归档根目录：{e}")
            return

        target_dir = root_path / safe_fs_segment(category) / safe_fs_segment(batch_name)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            st.error(f"无法创建目标目录：{target_dir}\n{e}")
            return

        _v3_clear_review_session_state()

        errors: list[str] = []
        n = len(uploaded_list)
        v3_review_stems: list[str] = []
        # 先固化展示名（与 session_state 下标对齐），再极速落盘，避免长耗时后 UploadedFile 缓存失效导致 WinError 2
        recording_labels: list[str] = [
            getattr(uf, "name", "") or f"recording_{i}" for i, uf in enumerate(uploaded_list)
        ]
        saved_audio_paths: list[Path | None] = [None] * n
        for i, uf in enumerate(uploaded_list):
            fname = recording_labels[i]
            audio_path = target_dir / fname
            try:
                audio_path.write_bytes(uf.getvalue())
                saved_audio_paths[i] = audio_path.resolve()
            except OSError as e:
                errors.append(f"{fname}: 抢救落盘失败（无法写入归档目录）{e}")
            except Exception as e:
                errors.append(f"{fname}: 抢救落盘失败 {e!s}")

        progress_bar = st.progress(0)

        custom_roles = (
            (st.session_state.get("custom_roles_other") or "").strip()
            if category == OTHER_SCENE_KEY
            else ""
        )

        html_opts = HtmlExportOptions(
            footer_watermark=(html_watermark or "").strip(),
            content_replace_map=html_mask_map if mask_html_body else None,
            show_generated_timestamp=True,
        )

        st.session_state.pop("v7_qa_truncation_warn", None)

        for i in range(n):
            fname = recording_labels[i]
            stem = Path(fname).stem
            audio_path = saved_audio_paths[i]

            if audio_path is None:
                progress_bar.progress((i + 1) / n)
                continue

            try:
                with st.status(
                    "🚀 正在执行 AI 深度复盘...",
                    expanded=True,
                ) as status:
                    raw_bytes = audio_path.read_bytes()
                    orig_len = len(raw_bytes)
                    orig_size_mb = orig_len / (1024 * 1024)
                    status.write(
                        f"📥 接收原始文件：大小 {orig_size_mb:.2f} MB"
                    )
                    if orig_len < 10 * 1024 * 1024:
                        status.write("✅ 文件极轻量，免压缩直通 ASR。")
                        work_audio = audio_path
                    else:
                        status.write(
                            "⚙️ 启动智能音频网关 (抽离视频轨 & 语音降采样)..."
                        )
                        cres = smart_compress_media(
                            raw_bytes, filename_hint=fname
                        )
                        if cres.did_compress:
                            new_size_mb = len(cres.data) / (1024 * 1024)
                            ratio = (1.0 - len(cres.data) / max(1, orig_len)) * 100.0
                            st.success(
                                f"🚀 极致压缩完成！新大小：{new_size_mb:.2f} MB "
                                f"(体积缩减 {ratio:.1f}%)"
                            )
                            gw = target_dir / f"{stem}_v62_asr_gateway.mp3"
                            gw.write_bytes(cres.data)
                            work_audio = gw.resolve()
                        else:
                            st.warning(
                                "⚠️ 压缩遇到特殊格式，已安全回退至原文件处理。"
                            )
                            work_audio = audio_path

                    # ASR 缓存检查：若「提取文字稿」时已转写过，直接复用，避免重复计费
                    file_hash = _file_md5(raw_bytes)
                    asr_cache: dict = st.session_state.setdefault("asr_cache", {})
                    cached_entry = asr_cache.get(file_hash)
                    cached_words_models = None
                    if cached_entry:
                        cached_words_models = [
                            TranscriptionWord.model_validate(w)
                            for w in cached_entry["words"]
                        ]
                        status.write("✅ 检测到本条录音的转写缓存，跳过云端 ASR，节省资源。")
                    else:
                        status.write(
                            "里程碑：云端转写 → 敏感词脱敏 → DeepSeek 多维度 QA 对齐（结构化 JSON）→ 初稿进入审查台。"
                        )

                    per_iv = (st.session_state.get(f"batch_iv_{i}") or "").strip()
                    sniper_json = _batch_sniper_targets_json(i)

                    explicit_context = build_explicit_context(
                        category,
                        project_name,
                        per_iv,
                        session_notes="",
                        sniper_targets_json=sniper_json,
                        recording_label=fname,
                        custom_roles_other=custom_roles,
                    )

                    trans_json = target_dir / f"{stem}_transcription.json"
                    analysis_json = target_dir / f"{stem}_analysis_report.json"
                    html_stem = apply_html_filename_masks(stem, html_mask_map)
                    html_name = f"{html_stem}_复盘报告.html"
                    html_path = target_dir / html_name

                    qa_text = batch_qa_texts[i]

                    params = PitchFileJobParams(
                        transcription_json_path=trans_json,
                        analysis_json_path=analysis_json,
                        html_output_path=html_path,
                        sensitive_words=sensitive_words,
                        explicit_context=explicit_context,
                        qa_text=qa_text,
                        model_choice="deepseek",
                        html_export_options=html_opts,
                    )

                    def _pipe_status(m: str) -> None:
                        status.update(label=m, state="running")
                        if "QA 补充材料字数超载" in m:
                            st.session_state["v7_qa_truncation_warn"] = m

                    words, report = run_pitch_file_job(
                        work_audio,
                        params,
                        on_status=_pipe_status,
                        skip_html_export=True,
                        cached_words=cached_words_models,
                    )

                    # 首次转写后写入缓存，供后续同文件操作复用
                    if not cached_entry:
                        asr_cache[file_hash] = {
                            "words": [w.model_dump() for w in words],
                            "plain": format_transcript_plain_by_speaker(words),
                        }

                    draft = report.model_dump()
                    for _rp in draft.get("risk_points") or []:
                        _rp.setdefault("_rid", uuid.uuid4().hex[:16])

                    st.session_state[f"report_draft_{stem}"] = draft
                    st.session_state[f"words_{stem}"] = [w.model_dump() for w in words]
                    st.session_state[f"v3_ctx_{stem}"] = {
                        "audio_path": str(work_audio),
                        "analysis_json": str(analysis_json),
                        "html_path": str(html_path),
                        "project_name": project_name,
                        "interviewee": per_iv,
                        "watermark": (html_watermark or "").strip(),
                        "mask_html_body": bool(mask_html_body),
                        "html_mask_map": dict(html_mask_map),
                    }
                    v3_review_stems.append(stem)

                    status.update(
                        label=f"✅ {fname} AI 初稿已就绪，请在下方审查台锁定后导出 HTML",
                        state="complete",
                    )
            except Exception as e:
                errors.append(f"{fname}: {e!s}")

            progress_bar.progress((i + 1) / n)

        progress_bar.progress(1.0)

        try:
            n_gc = sweep_stale_intermediate_json(root_path)
            if n_gc:
                logging.getLogger("garbage_collector").info(
                    "批次结束后 GC：已删除 %d 个过期中间 JSON", n_gc
                )
        except Exception:
            logging.getLogger("garbage_collector").exception("批次结束后 GC 失败")

        st.session_state["v3_review_stems"] = v3_review_stems

        qa_trunc_warn = st.session_state.pop("v7_qa_truncation_warn", None)
        if qa_trunc_warn:
            st.warning(qa_trunc_warn)

        if errors:
            st.warning("部分文件处理失败：")
            for e in errors:
                st.error(e)

        if len(errors) < n:
            st.balloons()
            st.success(
                f"✅ AI 初稿与转写已归档至：**{target_dir}**（JSON 已写；"
                f"**最终 HTML 需在下方审查台锁定后生成**）"
            )
            _v3_render_review_workbench()
        else:
            st.error("全部任务失败，请检查上方错误与 API 配置。")

    except Exception as e:
        logging.getLogger("ai_pitch_coach.ui").exception("主流程未捕获异常")
        st.error(f"系统在处理过程中发生意外错误，已记录到诊断日志：{e!s}")
        st.caption("若需技术支持，请下载下方 `debug.log` 并附上复现步骤。")
        st.download_button(
            label="🆘 下载系统诊断日志",
            data=read_debug_log_bytes(),
            file_name="debug.log",
            mime="text/plain",
            key="v4_fatal_debug_log_download",
        )


if __name__ == "__main__":
    main()
