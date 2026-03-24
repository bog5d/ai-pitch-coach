"""
AI 路演与访谈复盘系统 — Streamlit 企业级控制台（批量归档 + 动态路径）。
运行：在项目根目录执行  streamlit run app.py
依赖：pip install streamlit（及项目既有 transcriber / llm_judge / report_builder 依赖）
"""
from __future__ import annotations

import json
import os
import sys
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

from runtime_paths import get_writable_app_root

_ROOT = Path(get_resource_path(".")).resolve()
_ENV_PATH = get_writable_app_root() / ".env"

import streamlit as st
from dotenv import load_dotenv, set_key
from openai import APIError, OpenAI

load_dotenv(_ENV_PATH)

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
from report_builder import HtmlExportOptions

_SCENE_SELECT_PLACEHOLDER = "—— 请先选择业务场景 ——"

MODE_SINGLE = "单条（整场共用上下文）"
MODE_BATCH = "批量（每文件单独被访谈人与备注）"


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


def _parse_sensitive_words(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


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

    with st.sidebar:
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
                    st.session_state.api_dual_ok = False
                    st.error("❌ 请先完整填写两个 API Key。")
                else:
                    try:
                        set_key(str(_ENV_PATH), "DASHSCOPE_API_KEY", ds)
                        set_key(str(_ENV_PATH), "DEEPSEEK_API_KEY", dk)
                        load_dotenv(_ENV_PATH, override=True)
                        os.environ["DASHSCOPE_API_KEY"] = ds
                        os.environ["DEEPSEEK_API_KEY"] = dk
                        with st.status("🔌 正在连通性自检...", expanded=True) as status:
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
                            status.update(label="自检完成", state="complete")
                        if ok_a and ok_b:
                            st.session_state.api_dual_ok = True
                            st.success("✅ 双路 API 已连通，可开始生成报告。")
                        else:
                            st.session_state.api_dual_ok = False
                            parts: list[str] = []
                            if not ok_a:
                                parts.append(f"阿里云：{err_a}")
                            if not ok_b:
                                parts.append(f"DeepSeek：{err_b}")
                            st.error("❌ 连通性未通过：" + "；".join(parts))
                    except Exception as e:
                        st.session_state.api_dual_ok = False
                        st.error(f"❌ 保存或测试失败：{e!s}")

        if st.session_state.get("api_dual_ok"):
            st.success("✅ API：双路已验证")
        else:
            st.caption("⚠️ 须「保存并测试连接」全部绿灯后方可生成报告。")

        sensitive_words_input = st.text_area(
            "🔒 敏感词汇黑名单 (逗号分隔)",
            value="福创投, 迪策, 净利润",
            help="在调用大模型前，对转写词文本做替换为 ***。",
        )

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

    st.title("🚀 AI 路演与访谈复盘系统")

    if not st.session_state.get("api_dual_ok", False):
        st.warning(
            "⚠️ 请先在左侧侧边栏「🔑 首次使用请配置 API 密钥」中填写 Key，"
            "点击「💾 保存并测试连接」直至双路绿灯后，方可开始生成报告。"
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

    process_mode = st.radio(
        "处理模式",
        options=[MODE_SINGLE, MODE_BATCH],
        index=0,
        horizontal=True,
        help=(
            "单条：整场共用一名被访谈人，适合一场录音或多人同语境。"
            "批量：每个上传文件单独填写被访谈人与备注，适合一人一段录音。"
        ),
        key="process_mode_radio",
    )

    interviewee = ""
    if process_mode == MODE_SINGLE:
        interviewee = st.text_input(
            "被访谈人（必填）",
            placeholder="例如：高管姓名或对内代号",
            help="本场录音对应的被访谈对象，写入 AI 上下文。",
            key="interviewee_single",
        )
    else:
        st.caption(
            "批量模式：上传音频后，在下方「逐文件信息」中为 **每个文件** 填写被访谈人（必填）与备注（可选），"
            "将分别写入该段转写的 AI 上下文。"
        )

    if category == OTHER_SCENE_KEY:
        st.text_input(
            "请填写具体双方身份（必填）",
            placeholder="例如：供应商质量负责人 vs 买方投资机构",
            key="custom_roles_other",
        )

    tab_qa_file, tab_qa_dir = st.tabs(["上传QA文件", "选择参考文件夹"])
    with tab_qa_file:
        qa_upload = st.file_uploader(
            "上传参考 QA（可多选）",
            type=["txt", "md", "pdf", "docx", "xlsx"],
            accept_multiple_files=True,
            key="qa_file_upload",
            help=(
                "支持 txt、md、pdf、docx、xlsx。为保证系统稳定，暂不支持 PPT；"
                "若为 PPT 请另存为 PDF 后上传。最大读取前 15000 字。"
            ),
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

    if process_mode == MODE_BATCH and uploaded_list:
        st.subheader("逐文件信息（进入 AI 上下文）")
        for idx, uf in enumerate(uploaded_list):
            st.markdown(f"**文件 {idx + 1}：** `{uf.name}`")
            c_iv, c_note = st.columns(2)
            with c_iv:
                st.text_input(
                    "被访谈人（必填）",
                    key=f"batch_iv_{idx}",
                    placeholder="本段录音对应的对象",
                    help="仅作用于当前这一条录音的打分与复盘。",
                )
            with c_note:
                st.text_input(
                    "本段备注（可选）",
                    key=f"batch_note_{idx}",
                    placeholder="角色、场次、关注点等",
                    help="写入 AI 上下文本段补充说明。",
                )

    st.info(
        "💡 **建议**：开始生成前，在上方「上传QA文件」中上传 **内部 QA 口径**、**访谈纪要** 或 **对客口径 PDF/Word**，"
        "便于 AI 对照标准找茬；未上传时仍会生成报告，但对齐深度可能下降。"
    )

    run = st.button(
        "开始生成批量复盘报告",
        type="primary",
        disabled=not st.session_state.get("api_dual_ok", False),
    )

    if not run:
        st.info("配置侧边栏与业务场景，上传音频后点击按钮开始。")
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

    if process_mode == MODE_SINGLE:
        iv_single = (st.session_state.get("interviewee_single") or "").strip()
        if not iv_single:
            st.error("单条模式下请填写「被访谈人」（必填）。")
            return
        interviewee = iv_single
    else:
        for idx in range(len(uploaded_list)):
            iv = (st.session_state.get(f"batch_iv_{idx}") or "").strip()
            if not iv:
                st.error(
                    f"批量模式下请为文件「{uploaded_list[idx].name}」填写被访谈人（必填）。"
                )
                return

    if category == OTHER_SCENE_KEY:
        cr = (st.session_state.get("custom_roles_other") or "").strip()
        if not cr:
            st.error('业务大类为「05_其他」时，必须填写「具体双方身份」。')
            return

    sensitive_words = _parse_sensitive_words(sensitive_words_input)
    html_mask_map = _merge_html_filename_masks(filename_mask_input)
    project_name = (batch_name or "").strip()

    qa_files_list: list = []
    if qa_upload is not None:
        qa_files_list = list(qa_upload) if isinstance(qa_upload, (list, tuple)) else [qa_upload]
    qa_text = extract_text_from_files(qa_files_list, max_chars=15000)

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

    progress_bar = st.progress(0)
    errors: list[str] = []

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

    n = len(uploaded_list)
    for i, uf in enumerate(uploaded_list):
        fname = uf.name
        stem = Path(fname).stem

        audio_path = target_dir / fname
        try:
            audio_path.write_bytes(uf.getvalue())
        except OSError as e:
            errors.append(f"{fname}: 保存文件失败 {e}")
            progress_bar.progress((i + 1) / n)
            continue

        try:
            with st.status(
                "🚀 正在启动自动化处理流水线...",
                expanded=True,
            ) as status:
                status.update(
                    label=f"🎧 正在竖起耳朵听 {uf.name} (音频转写中，请耐心喝口水)...",
                    state="running",
                )

                if process_mode == MODE_SINGLE:
                    per_iv = (interviewee or "").strip()
                    per_note = ""
                else:
                    per_iv = (st.session_state.get(f"batch_iv_{i}") or "").strip()
                    per_note = (st.session_state.get(f"batch_note_{i}") or "").strip()

                explicit_context = build_explicit_context(
                    category,
                    project_name,
                    per_iv,
                    session_notes=per_note,
                    recording_label=uf.name,
                    custom_roles_other=custom_roles,
                )

                trans_json = target_dir / f"{stem}_transcription.json"
                analysis_json = target_dir / f"{stem}_analysis_report.json"
                html_stem = apply_html_filename_masks(stem, html_mask_map)
                html_name = f"{html_stem}_复盘报告.html"
                html_path = target_dir / html_name

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

                run_pitch_file_job(
                    audio_path,
                    params,
                    on_status=lambda m: status.update(label=m, state="running"),
                )

                status.update(
                    label=f"✅ {uf.name} 报告新鲜出炉！",
                    state="complete",
                )
        except Exception as e:
            errors.append(f"{fname}: {e!s}")

        progress_bar.progress((i + 1) / n)

    progress_bar.progress(1.0)

    if errors:
        st.warning("部分文件处理失败：")
        for e in errors:
            st.error(e)

    if len(errors) < n:
        st.balloons()
        st.success(f"✅ 所有报告已生成并归档至：**{target_dir}**")
    else:
        st.error("全部任务失败，请检查上方错误与 API 配置。")


if __name__ == "__main__":
    main()
