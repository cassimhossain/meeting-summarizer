"""
app.py — Streamlit UI for the bilingual Meeting Summarizer

Features:
• Audio upload (mp3, wav, m4a, ogg, flac, webm)
• Language picker for input audio (auto / English / Urdu)
• Output language picker (English / Urdu / Both)
• Whisper model size selector (small recommended for Urdu)
• Live progress feedback during transcription and summarization
• Tabbed view: Summary | Action Items | Decisions | Risks | Transcript
• Download buttons for PDF, JSON, and plain-text transcript
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import streamlit as st

from transcriber import (
    LANGUAGE_CODES,
    save_uploaded_file,
    transcribe_audio,
)
from summarizer import summarize_transcript
from pdf_generator import generate_pdf

# ── Page config ────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Meeting Summarizer",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Custom CSS for a cleaner look ─────────────────────────────────────────

st.markdown("""
<style>
    .stApp {background-color: #FAFAFA;}
    .main-title {
        font-size: 2.4rem;
        font-weight: 700;
        color: #1E40AF;
        margin-bottom: 0;
    }
    .main-subtitle {
        color: #6B7280;
        font-size: 1rem;
        margin-bottom: 1.5rem;
    }
    .stat-card {
        background: white;
        padding: 1rem;
        border-radius: 8px;
        border: 1px solid #E5E7EB;
        text-align: center;
    }
    .stat-number {
        font-size: 2rem;
        font-weight: 700;
        color: #1E40AF;
    }
    .stat-label {
        color: #6B7280;
        font-size: 0.85rem;
    }
    .urdu-text {
        font-family: 'Noto Nastaliq Urdu', 'Jameel Noori Nastaleeq', serif;
        direction: rtl;
        text-align: right;
        font-size: 1.1rem;
        line-height: 2;
    }
    .priority-high {color: #EF4444; font-weight: 600;}
    .priority-medium {color: #F59E0B; font-weight: 600;}
    .priority-low {color: #10B981; font-weight: 600;}
</style>
""", unsafe_allow_html=True)


# ── Session state ──────────────────────────────────────────────────────────

if "transcript" not in st.session_state:
    st.session_state.transcript = None
if "transcript_lang" not in st.session_state:
    st.session_state.transcript_lang = None
if "summary" not in st.session_state:
    st.session_state.summary = None
if "pdf_path" not in st.session_state:
    st.session_state.pdf_path = None


# ── Header ────────────────────────────────────────────────────────────────

st.markdown('<p class="main-title">🎙️ Meeting Summarizer</p>',
            unsafe_allow_html=True)
st.markdown(
    '<p class="main-subtitle">Transform hours of meetings into '
    'concise, actionable summaries — now with English + Urdu support.</p>',
    unsafe_allow_html=True,
)


# ── Sidebar: Configuration ────────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ Settings")

    st.subheader("🎤 Input Audio")
    input_lang_label = st.selectbox(
        "Audio Language",
        options=["Auto-detect", "English", "Urdu"],
        index=0,
        help="Pick 'Auto-detect' for mixed Urdu+English meetings.",
    )
    input_lang = LANGUAGE_CODES[input_lang_label.lower().replace("-detect", "")]

    model_size = st.selectbox(
        "Whisper Model",
        options=["tiny", "base", "small", "medium"],
        index=2,
        help=(
            "Larger = more accurate but slower.\n"
            "• tiny/base — English only, fast\n"
            "• small — recommended for Urdu (default)\n"
            "• medium — best Urdu accuracy, slower"
        ),
    )

    translate_to_english = st.checkbox(
        "Translate to English during transcription",
        value=False,
        help=(
            "If ON, Urdu speech is translated directly to English text "
            "by Whisper. Useful if you only need an English summary."
        ),
    )

    st.divider()

    st.subheader("📝 Output Summary")
    output_lang_label = st.selectbox(
        "Summary Language",
        options=["English", "Urdu", "Both (Bilingual)"],
        index=0,
        help=(
            "• English — Standard English summary\n"
            "• Urdu — Full summary in Urdu (Nastaliq script)\n"
            "• Both — English summary followed by Urdu mirror section"
        ),
    )
    output_lang = output_lang_label.lower().split()[0]  # 'english'/'urdu'/'both'

    st.divider()

    st.markdown(
        '<p style="color:#6B7280; font-size:0.8rem;">'
        'Powered by Whisper + LLaMA 3.3 (via Groq) + ReportLab'
        '</p>',
        unsafe_allow_html=True,
    )


# ── Main: Upload section ──────────────────────────────────────────────────

st.subheader("1. Upload Meeting Audio")

uploaded_file = st.file_uploader(
    "Drop your audio file here",
    type=["mp3", "wav", "m4a", "ogg", "flac", "webm", "mp4"],
    help="Supported formats: MP3, WAV, M4A, OGG, FLAC, WebM, MP4",
)

if uploaded_file is not None:
    file_size_mb = uploaded_file.size / (1024 * 1024)
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.metric("Filename", uploaded_file.name)
    with col_b:
        st.metric("Size", f"{file_size_mb:.1f} MB")
    with col_c:
        st.metric("Type", uploaded_file.type or "audio")

    st.audio(uploaded_file)


# ── Transcribe button ─────────────────────────────────────────────────────

st.subheader("2. Transcribe & Summarize")

col_transcribe, col_summarize = st.columns(2)

with col_transcribe:
    if st.button("🎯 Transcribe Audio", type="primary",
                 use_container_width=True,
                 disabled=uploaded_file is None):
        try:
            with st.status("Processing audio…", expanded=True) as status:
                file_path = save_uploaded_file(uploaded_file)

                progress_msgs = []

                def _on_progress(msg: str) -> None:
                    progress_msgs.append(msg)
                    status.write(msg)

                result = transcribe_audio(
                    file_path,
                    model_size=model_size,
                    language=input_lang,
                    translate_to_english=translate_to_english,
                    progress=_on_progress,
                )

                st.session_state.transcript = result["text"]
                st.session_state.transcript_lang = result["language"]
                st.session_state.summary = None  # invalidate old summary

                status.update(label="✅ Transcription complete!",
                              state="complete", expanded=False)

            st.success(
                f"Transcribed successfully. "
                f"Detected language: **{result['language']}** • "
                f"Duration: ~{int(result.get('duration', 0))}s"
            )

        except Exception as e:
            st.error(f"Transcription failed: {e}")

with col_summarize:
    if st.button("✨ Generate Summary", type="primary",
                 use_container_width=True,
                 disabled=st.session_state.transcript is None):
        try:
            with st.status("Analyzing meeting…", expanded=True) as status:
                def _on_progress(msg: str) -> None:
                    status.write(msg)

                summary = summarize_transcript(
                    st.session_state.transcript,
                    progress=_on_progress,
                    output_language=output_lang,
                )
                st.session_state.summary = summary

                status.update(label="✅ Summary ready!",
                              state="complete", expanded=False)

            st.success("Meeting analyzed successfully!")

        except Exception as e:
            st.error(f"Summarization failed: {e}")


# ── Results display ──────────────────────────────────────────────────────

if st.session_state.summary:
    summary = st.session_state.summary
    st.divider()

    # Title + meta strip
    st.markdown(f"## {summary.get('meeting_title', 'Meeting Summary')}")
    st.caption(
        f"**Type:** {summary.get('meeting_type', 'meeting').title()} • "
        f"**Sentiment:** {summary.get('sentiment', 'neutral').title()} • "
        f"**Duration:** {summary.get('duration_estimate', 'Unknown')}"
    )

    # Stats row
    stats = summary.get("stats", {})
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.markdown(
            f'<div class="stat-card"><div class="stat-number">'
            f'{len(summary.get("attendees", []))}</div>'
            f'<div class="stat-label">Attendees</div></div>',
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f'<div class="stat-card"><div class="stat-number">'
            f'{stats.get("action_item_count", 0)}</div>'
            f'<div class="stat-label">Action Items</div></div>',
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f'<div class="stat-card"><div class="stat-number">'
            f'{stats.get("decision_count", 0)}</div>'
            f'<div class="stat-label">Decisions</div></div>',
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            f'<div class="stat-card"><div class="stat-number">'
            f'{stats.get("open_question_count", 0)}</div>'
            f'<div class="stat-label">Open Qs</div></div>',
            unsafe_allow_html=True,
        )
    with c5:
        st.markdown(
            f'<div class="stat-card"><div class="stat-number">'
            f'{stats.get("risk_count", 0)}</div>'
            f'<div class="stat-label">Risks</div></div>',
            unsafe_allow_html=True,
        )

    st.write("")  # spacing

    # Tabs
    tabs = st.tabs([
        "📋 Summary", "✅ Action Items", "🎯 Decisions",
        "❓ Open Questions", "⚠️ Risks", "📜 Transcript",
    ])

    # — Summary tab —
    with tabs[0]:
        st.markdown("### Executive Summary")
        st.write(summary.get("summary", "No summary available."))

        if summary.get("attendees"):
            st.markdown("### Attendees")
            st.write(" • ".join(summary["attendees"]))

        if summary.get("key_topics"):
            st.markdown("### Key Topics")
            st.write(" · ".join(summary["key_topics"]))

        if summary.get("next_steps"):
            st.markdown("### Next Steps")
            for step in summary["next_steps"]:
                st.markdown(f"- {step}")

        # Urdu mirror if 'both' mode
        if summary.get("summary_ur"):
            st.divider()
            st.markdown("### اردو خلاصہ")
            st.markdown(
                f'<div class="urdu-text">{summary["summary_ur"]}</div>',
                unsafe_allow_html=True,
            )
            if summary.get("next_steps_ur"):
                st.markdown("### اگلے اقدامات")
                for step in summary["next_steps_ur"]:
                    st.markdown(
                        f'<div class="urdu-text">• {step}</div>',
                        unsafe_allow_html=True,
                    )

    # — Action Items tab —
    with tabs[1]:
        items = summary.get("action_items", [])
        if not items:
            st.info("No action items identified.")
        else:
            for i, item in enumerate(items, 1):
                priority_class = f"priority-{item.get('priority', 'medium').lower()}"
                with st.container(border=True):
                    st.markdown(f"**{i}. {item.get('task', '')}**")
                    cols = st.columns([2, 2, 1])
                    with cols[0]:
                        st.markdown(
                            f"👤 **Owner:** {item.get('owner', 'Unassigned')}"
                        )
                    with cols[1]:
                        st.markdown(
                            f"📅 **Due:** {item.get('due_date', 'Not specified')}"
                        )
                    with cols[2]:
                        st.markdown(
                            f'<span class="{priority_class}">'
                            f'{item.get("priority", "Medium")}</span>',
                            unsafe_allow_html=True,
                        )
                    if item.get("context"):
                        st.caption(item["context"])

    # — Decisions tab —
    with tabs[2]:
        decisions = summary.get("decisions", [])
        if not decisions:
            st.info("No decisions recorded.")
        else:
            for i, d in enumerate(decisions, 1):
                with st.container(border=True):
                    st.markdown(f"**{i}. {d.get('decision', '')}**")
                    if d.get("rationale"):
                        st.caption(f"💡 *Rationale:* {d['rationale']}")
                    if d.get("decided_by"):
                        st.caption(f"👤 *Decided by:* {d['decided_by']}")
                    if d.get("impact"):
                        st.caption(f"📊 *Impact:* {d['impact']}")

    # — Open Questions tab —
    with tabs[3]:
        questions = summary.get("open_questions", [])
        if not questions:
            st.info("No open questions.")
        else:
            for q in questions:
                with st.container(border=True):
                    st.markdown(f"❓ **{q.get('question', '')}**")
                    st.caption(
                        f"Assigned to: {q.get('assigned_to', 'Team')} • "
                        f"Urgency: {q.get('urgency', 'Medium')}"
                    )

    # — Risks tab —
    with tabs[4]:
        risks = summary.get("risks", [])
        if not risks:
            st.info("No risks identified.")
        else:
            for r in risks:
                with st.container(border=True):
                    likelihood = r.get("likelihood", "Unknown")
                    priority_class = f"priority-{likelihood.lower()}" \
                        if likelihood.lower() in ("high", "medium", "low") \
                        else ""
                    st.markdown(
                        f"⚠️ **{r.get('risk', '')}** "
                        f'<span class="{priority_class}">[{likelihood}]</span>',
                        unsafe_allow_html=True,
                    )
                    if r.get("mitigation"):
                        st.caption(f"🛡️ *Mitigation:* {r['mitigation']}")

    # — Transcript tab —
    with tabs[5]:
        st.markdown("### Full Transcript")
        st.caption(
            f"Detected language: **{st.session_state.transcript_lang}**"
        )
        # Detect Urdu in transcript and render with RTL CSS class
        is_urdu = any(
            "\u0600" <= ch <= "\u06FF"
            for ch in (st.session_state.transcript or "")
        )
        if is_urdu:
            st.markdown(
                f'<div class="urdu-text">{st.session_state.transcript}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.text_area(
                "Transcript",
                st.session_state.transcript,
                height=400,
                label_visibility="collapsed",
            )

    # ── Downloads ─────────────────────────────────────────────────────────

    st.divider()
    st.subheader("3. Download Reports")

    dcol1, dcol2, dcol3 = st.columns(3)

    with dcol1:
        if st.button("📄 Generate PDF Report", use_container_width=True):
            try:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                pdf_path = f"output/meeting_{ts}.pdf"
                generate_pdf(
                    summary,
                    output_path=pdf_path,
                    transcript=st.session_state.transcript,
                )
                st.session_state.pdf_path = pdf_path
                st.success("PDF generated!")
            except Exception as e:
                st.error(f"PDF generation failed: {e}")

    with dcol2:
        json_str = json.dumps(summary, indent=2, ensure_ascii=False)
        st.download_button(
            "💾 Download JSON",
            data=json_str,
            file_name=f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            mime="application/json",
            use_container_width=True,
        )

    with dcol3:
        if st.session_state.transcript:
            st.download_button(
                "📝 Download Transcript",
                data=st.session_state.transcript,
                file_name=f"transcript_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                mime="text/plain",
                use_container_width=True,
            )

    # PDF download (separate so we can show it after generation)
    if st.session_state.pdf_path and os.path.exists(st.session_state.pdf_path):
        with open(st.session_state.pdf_path, "rb") as f:
            st.download_button(
                "⬇️ Download PDF Report",
                data=f.read(),
                file_name=os.path.basename(st.session_state.pdf_path),
                mime="application/pdf",
                use_container_width=True,
            )


# ── Footer ────────────────────────────────────────────────────────────────

st.divider()
st.caption(
    "Built with ❤️ in Pakistan • "
    "[GitHub](https://github.com/cassimhossain/meeting-summarizer) • "
    "Whisper + LLaMA 3.3"
)