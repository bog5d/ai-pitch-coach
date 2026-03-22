"""
AI 路演与访谈复盘系统 — Streamlit 企业级控制台（批量归档 + 动态路径）。
运行：在项目根目录执行  streamlit run app.py
依赖：pip install streamlit（及项目既有 transcriber / llm_judge / report_builder 依赖）
"""
from __future__ import annotations

import json
import os
import re
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
from llm_judge import evaluate_pitch
from report_builder import generate_html_report
from schema import TranscriptionWord
from transcriber import transcribe_audio

# ---------------------------------------------------------------------------
# 业务场景与默认双方角色（05 需用户手填）
# ---------------------------------------------------------------------------
SCENE_MAP: dict[str, str] = {
    "01_机构路演": "被尽调企业的投融资负责人 vs 投资机构",
    "02_高管访谈": "被尽调企业的高管 vs 投资机构",
    "03_客户访谈": "被尽调企业的客户 vs 投资机构",
    "04_供应商访谈": "被尽调企业的供应商 vs 投资机构",
    "05_其他(需手动输入)": "自定义",
}

OTHER_SCENE_KEY = "05_其他(需手动输入)"


def _safe_fs_segment(name: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\n\r\t]', "_", name.strip())
    return s or "未命名批次"


def _env_configured(key: str) -> bool:
    v = os.getenv(key)
    return bool(v and str(v).strip())


def _parse_sensitive_words(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


def _mask_words_for_llm(
    words: list[TranscriptionWord],
    sensitive_words: list[str],
) -> list[TranscriptionWord]:
    if not sensitive_words:
        return words
    out: list[TranscriptionWord] = []
    for w in words:
        t = w.text
        for kw in sensitive_words:
            if kw and kw in t:
                t = t.replace(kw, "***")
        if t != w.text:
            out.append(w.model_copy(update={"text": t}))
        else:
            out.append(w)
    return out


def _build_explicit_context(
    category: str,
    project_name: str,
) -> dict[str, str]:
    if category == OTHER_SCENE_KEY:
        roles = (st.session_state.get("custom_roles_other") or "").strip()
    else:
        roles = SCENE_MAP.get(category, "未指定")
    return {
        "biz_type": category,
        "exact_roles": roles or "未指定",
        "project_name": (project_name or "").strip() or "未指定",
    }


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

    col1, col2 = st.columns(2)
    with col1:
        category = st.selectbox(
            "业务大类",
            options=list(SCENE_MAP.keys()),
            index=0,
        )
    with col2:
        batch_name = st.text_input(
            "项目/批次名称",
            placeholder="例如：泰亚投资、福创投",
            help="将作为子文件夹名称的一部分。",
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

    run = st.button(
        "开始生成批量复盘报告",
        type="primary",
        disabled=not st.session_state.get("api_dual_ok", False),
    )

    if not run:
        st.info("配置侧边栏与业务场景，上传音频后点击按钮开始。")
        return

    if not uploaded:
        st.warning("请先上传至少一个音频文件。")
        return

    if category == OTHER_SCENE_KEY:
        cr = (st.session_state.get("custom_roles_other") or "").strip()
        if not cr:
            st.error('业务大类为「05_其他」时，必须填写「具体双方身份」。')
            return

    sensitive_words = _parse_sensitive_words(sensitive_words_input)
    project_name = (batch_name or "").strip()
    explicit_context = _build_explicit_context(category, project_name)

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

    target_dir = root_path / _safe_fs_segment(category) / _safe_fs_segment(batch_name)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        st.error(f"无法创建目标目录：{target_dir}\n{e}")
        return

    progress_bar = st.progress(0)
    errors: list[str] = []

    n = len(uploaded)
    for i, uf in enumerate(uploaded):
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

                trans_json = target_dir / f"{stem}_transcription.json"
                words = transcribe_audio(audio_path, out_json_path=trans_json)

                status.update(
                    label=f"🕵️ 听写完毕！共抓取 {len(words)} 个词汇。正在执行商业机密脱敏打码...",
                    state="running",
                )
                words_for_llm = _mask_words_for_llm(words, sensitive_words)

                status.update(
                    label="🧠 正在请出顶级 VC 大脑，戴上老花镜逐字找茬中 (最耗时的一步，让子弹飞一会儿)...",
                    state="running",
                )
                report = evaluate_pitch(
                    words_for_llm,
                    model_choice="deepseek",
                    explicit_context=explicit_context,
                    qa_text=qa_text,
                )

                status.update(
                    label="✂️ 找茬完毕！正在疯狂裁剪原声音频，为您装订绝美复盘报告...",
                    state="running",
                )
                analysis_json = target_dir / f"{stem}_analysis_report.json"
                analysis_json.write_text(
                    json.dumps(report.model_dump(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                html_name = f"{stem}_复盘报告.html"
                html_path = target_dir / html_name
                generate_html_report(audio_path, words, report, html_path)

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
