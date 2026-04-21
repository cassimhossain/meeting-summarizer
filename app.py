import streamlit as st
import os
from datetime import datetime
from transcriber import transcribe_audio, save_uploaded_file
from summarizer import summarize_transcript
from pdf_generator import generate_pdf_report
 
# ── Page config ──────────────────────────────────────────────
st.set_page_config(
    page_title='AI Meeting Summarizer',
    page_icon='🎧',
    layout='wide'
)
 
# ── Sidebar ──────────────────────────────────────────────────
with st.sidebar:
    st.title('🎧 AI Meeting Summarizer')
    st.markdown('---')
    st.markdown('**Powered by:**')
    st.markdown('✓ OpenAI Whisper (local)')
    st.markdown('✓ LLaMA 3.1 70B via Groq')
    st.markdown('✓ 100% Free Stack')
    st.markdown('---')
    model_size = st.selectbox(
        'Whisper Model',
        ['base', 'small', 'tiny'],
        help='base = recommended. small = more accurate. tiny = fastest.'
    )
    meeting_date = st.date_input('Meeting Date', datetime.today())
    st.markdown('---')
    st.caption('Built with Python + Streamlit')
 
# ── Main page ────────────────────────────────────────────────
st.title('📋 AI Meeting Summarizer')
st.markdown('Upload a meeting recording. Get action items, decisions, and a PDF.')
st.markdown('---')
 
uploaded_file = st.file_uploader(
    'Upload Meeting Audio or Video',
    type=['mp3', 'mp4', 'wav', 'm4a', 'ogg', 'flac'],
    help='Supported: MP3, MP4, WAV, M4A, OGG, FLAC'
)
 
if uploaded_file:
    st.success(f'File ready: {uploaded_file.name} ({uploaded_file.size/1024:.1f} KB)')
 
    if st.button('🚀 Transcribe and Summarize', use_container_width=True):
 
        # Step 1: Save file to disk
        with st.spinner('Saving file...'):
            file_path = save_uploaded_file(uploaded_file)
 
        # Step 2: Transcribe with Whisper
        with st.spinner(f'Transcribing with Whisper ({model_size})... may take 1-3 min.'):
            try:
                result = transcribe_audio(file_path, model_size=model_size)
                transcript = result['text']
                st.success(f'Transcription done! ({len(transcript.split())} words)')
            except Exception as e:
                st.error(f'Transcription failed: {e}')
                st.stop()
 
        # Step 3: Summarize with LLaMA 3
        with st.spinner('Analyzing with LLaMA 3...'):
            try:
                summary = summarize_transcript(transcript)
                st.success('Analysis complete!')
            except Exception as e:
                st.error(f'Summarization failed: {e}')
                st.stop()
 
        # Step 4: Display results
        st.markdown('---')
        st.header(summary.get('meeting_title', 'Meeting Summary'))
 
        c1, c2, c3, c4 = st.columns(4)
        c1.metric('Action Items',   len(summary.get('action_items', [])))
        c2.metric('Decisions',       len(summary.get('decisions', [])))
        c3.metric('Open Questions',  len(summary.get('open_questions', [])))
        c4.metric('Next Steps',      len(summary.get('next_steps', [])))
        st.markdown('---')
 
        col_l, col_r = st.columns([3, 2])
 
        with col_l:
            st.subheader('📝 Summary')
            st.write(summary.get('summary', ''))
 
            st.subheader('✅ Action Items')
            for item in summary.get('action_items', []):
                prio = {'High': '🔴', 'Medium': '🟡', 'Low': '🟢'}.get(item.get('priority','Medium'), '🟡')
                st.markdown(
                    f"{prio} **{item.get('task','')}**\n"
                    f"Owner: `{item.get('owner','Unassigned')}` | Due: `{item.get('due_date','TBD')}`"
                )
 
            st.subheader('💡 Decisions')
            for d in summary.get('decisions', []):
                st.markdown(f'✓ {d.get("decision","")}')
 
            st.subheader('❓ Open Questions')
            for q in summary.get('open_questions', []):
                st.markdown(f'• {q.get("question","")}')
 
        with col_r:
            st.subheader('💬 Raw Transcript')
            st.text_area('', transcript, height=400)
 
        # Step 5: Generate and offer PDF download
        st.markdown('---')
        with st.spinner('Generating PDF...'):
            pdf_bytes = generate_pdf_report(
                summary,
                meeting_date=meeting_date.strftime('%B %d, %Y')
            )
        fname = f'meeting_summary_{meeting_date.strftime("%Y%m%d")}.pdf'
        st.download_button(
            label='📥 Download PDF Report',
            data=pdf_bytes,
            file_name=fname,
            mime='application/pdf',
            use_container_width=True
        )
 
        # Cleanup temp file
        if os.path.exists(file_path):
            os.remove(file_path)
 
else:
    st.info('👆 Upload a meeting recording above to get started.')
    with st.expander('How it works'):
        st.markdown('''
        1. **Upload** any audio or video file (MP3, MP4, WAV, M4A)
        2. **Whisper** transcribes the speech to text locally on your machine
        3. **LLaMA 3** extracts action items, decisions, questions, next steps
        4. **Download** a clean PDF report with everything structured
        ''')
