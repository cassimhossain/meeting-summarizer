import whisper
import subprocess
import os
 
 
def convert_to_wav(input_path: str) -> str:
    """
    Convert any audio/video file to 16kHz mono WAV.
    Whisper works best with this exact format.
    Returns the path to the converted WAV file.
    """
    output_path = input_path.rsplit('.', 1)[0] + '_converted.wav'
    command = [
        'ffmpeg',
        '-i', input_path,   # input file path
        '-ar', '16000',     # sample rate 16kHz (Whisper needs this)
        '-ac', '1',         # mono audio (single channel)
        '-y',               # overwrite output file if it exists
        output_path
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f'FFmpeg conversion failed: {result.stderr}')
    return output_path
 
 
def transcribe_audio(file_path: str, model_size: str = 'base') -> dict:
    """
    Transcribe an audio file using local Whisper.
 
    model_size options:
        'tiny'   -> fastest, ~75MB,  lower accuracy
        'base'   -> recommended, ~140MB, good accuracy   <-- USE THIS
        'small'  -> better accuracy, ~460MB, slower
        'medium' -> high accuracy, needs decent GPU
 
    Returns dict with 'text' (full transcript) and 'language' detected.
    """
    # Step 1: Convert to WAV
    wav_path = convert_to_wav(file_path)
 
    # Step 2: Load Whisper model
    # First call downloads the model (~140MB for 'base') -- one time only
    print(f'Loading Whisper {model_size} model...')
    model = whisper.load_model(model_size)
 
    # Step 3: Transcribe
    # fp16=False forces CPU mode (avoids CUDA errors on laptops without GPU)
    print('Transcribing... this may take 1-3 minutes for longer recordings.')
    result = model.transcribe(wav_path, fp16=False)
 
    # Step 4: Clean up the temporary WAV file
    if os.path.exists(wav_path):
        os.remove(wav_path)
 
    return {
        'text': result['text'].strip(),
        'language': result.get('language', 'en')
    }
 
 
def save_uploaded_file(uploaded_file, save_dir: str = 'temp_audio') -> str:
    """
    Save a Streamlit uploaded file to disk and return its path.
    Streamlit gives us a BytesIO object -- we need a real file path
    for FFmpeg and Whisper to work with.
    """
    os.makedirs(save_dir, exist_ok=True)
    file_path = os.path.join(save_dir, uploaded_file.name)
    with open(file_path, 'wb') as f:
        f.write(uploaded_file.getbuffer())
    return file_path
