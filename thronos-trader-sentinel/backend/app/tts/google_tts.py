def synthesize(text: str, language: str = "en-US", voice: str = "en-US-Neural2-D") -> bytes:
    """Google Cloud Text-to-Speech (optional).

    Install:
      pip install google-cloud-texttospeech

    Credentials:
      export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service_account.json
    """
    try:
        from google.cloud import texttospeech  # type: ignore
    except Exception as e:
        raise RuntimeError("google-cloud-texttospeech is not installed") from e

    client = texttospeech.TextToSpeechClient()
    synthesis_input = texttospeech.SynthesisInput(text=text)

    # Use a named voice if available in your project/region.
    voice_params = texttospeech.VoiceSelectionParams(
        language_code=language,
        name=voice,
    )
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)

    response = client.synthesize_speech(
        input=synthesis_input,
        voice=voice_params,
        audio_config=audio_config,
    )
    return response.audio_content
