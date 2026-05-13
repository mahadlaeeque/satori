"""Voice, TTS, transliterate routes."""
from __future__ import annotations
import base64
import logging
import struct
from typing import Any, Dict
import requests as http_requests

from fastapi import APIRouter, Request, HTTPException
from google import genai
from google.genai import types

from services import state
from services.gemini import get_genai_client
from api._compat import FlaskReq, jsonify, adapt_body, to_response

load_settings = state.load_settings
logger = logging.getLogger(__name__)

router = APIRouter()


# Helper extracted verbatim from app.py
def _convert_to_wav(audio_data: bytes, mime_type: str) -> bytes:
    """Convert raw PCM audio from Gemini TTS to WAV format."""
    import struct as _struct

    # Parse mime type for parameters (e.g. "audio/L16;rate=24000")
    bits_per_sample = 16
    rate = 24000
    for param in mime_type.split(";"):
        param = param.strip()
        if param.lower().startswith("rate="):
            try:
                rate = int(param.split("=", 1)[1])
            except (ValueError, IndexError):
                pass
        elif param.startswith("audio/L"):
            try:
                bits_per_sample = int(param.split("L", 1)[1])
            except (ValueError, IndexError):
                pass

    num_channels = 1
    bytes_per_sample = bits_per_sample // 8
    block_align = num_channels * bytes_per_sample
    byte_rate = rate * block_align
    data_size = len(audio_data)
    chunk_size = 36 + data_size

    header = _struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", chunk_size, b"WAVE", b"fmt ", 16, 1,
        num_channels, rate, byte_rate, block_align, bits_per_sample,
        b"data", data_size
    )
    return header + audio_data




# =====================================================================
# /api/gemini-tts/voices
# =====================================================================
@router.get("/api/gemini-tts/voices")
def get_gemini_tts_voices():
    """Return the Gemini TTS voice list + current selection."""
    settings = load_settings()
    return jsonify({
        "voices": GEMINI_TTS_VOICES,
        "selected_voice": settings.get("gemini_tts_voice", "Leda"),
        "model": GEMINI_TTS_MODEL,
    })



# =====================================================================
# /api/gemini-tts/speak
# =====================================================================
@router.post("/api/gemini-tts/speak")
async def gemini_tts_speak(req: Request):
    body = await adapt_body(req)
    return to_response(_gemini_tts_speak(FlaskReq(req, body)))


def _gemini_tts_speak(request):
    """Generate speech using Gemini TTS. Returns WAV audio."""
    settings = load_settings()
    api_key = settings.get("gemini_api_key", "")
    if not api_key:
        return jsonify({"error": "Gemini API key not configured"}), 400

    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "No text provided"}), 400

    voice_name = data.get("voice_name") or settings.get("gemini_tts_voice", "Leda")
    tts_instructions = settings.get("tts_instructions", "")
    full_text = f"{tts_instructions}{text}" if tts_instructions else text

    try:
        client = genai.Client(api_key=api_key)

        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=full_text)],
            ),
        ]
        config = types.GenerateContentConfig(
            temperature=1,
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            ),
        )

        # Streaming: collect all audio chunks
        audio_data = b""
        mime_type = "audio/L16;rate=24000"
        for chunk in client.models.generate_content_stream(
            model=GEMINI_TTS_MODEL,
            contents=contents,
            config=config,
        ):
            if chunk.candidates and chunk.candidates[0].content and chunk.candidates[0].content.parts:
                part = chunk.candidates[0].content.parts[0]
                if part.inline_data and part.inline_data.data:
                    audio_data += part.inline_data.data
                    if part.inline_data.mime_type:
                        mime_type = part.inline_data.mime_type

        if not audio_data:
            return jsonify({"error": "No audio generated"}), 500

        wav_data = _convert_to_wav(audio_data, mime_type)
        return Response(wav_data, mimetype="audio/wav",
                        headers={"Cache-Control": "no-cache"})

    except Exception as e:
        print(f"[Gemini TTS] Error: {e}")
        return jsonify({"error": str(e)}), 500



# =====================================================================
# /api/gemini-tts/preview
# =====================================================================
@router.post("/api/gemini-tts/preview")
async def gemini_tts_preview(req: Request):
    body = await adapt_body(req)
    return to_response(_gemini_tts_preview(FlaskReq(req, body)))


def _gemini_tts_preview(request):
    """Preview a Gemini TTS voice with a bilingual sample sentence."""
    settings = load_settings()
    api_key = settings.get("gemini_api_key", "")
    if not api_key:
        return jsonify({"error": "Gemini API key not configured"}), 400

    data = request.get_json(silent=True) or {}
    voice_name = data.get("voice_name", "Leda")
    tts_instructions = settings.get("tts_instructions", "")

    preview_text = "Hello, I am Satori, your Capability Intelligence Agent. Mahad checked in today at 9:09 AM and left at 6:30 PM. Adeel ki attendance bhi complete hai."
    full_text = f"{tts_instructions}{preview_text}" if tts_instructions else preview_text

    try:
        client = genai.Client(api_key=api_key)

        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=full_text)],
            ),
        ]
        config = types.GenerateContentConfig(
            temperature=1,
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            ),
        )

        audio_data = b""
        mime_type = "audio/L16;rate=24000"
        for chunk in client.models.generate_content_stream(
            model=GEMINI_TTS_MODEL,
            contents=contents,
            config=config,
        ):
            if chunk.candidates and chunk.candidates[0].content and chunk.candidates[0].content.parts:
                part = chunk.candidates[0].content.parts[0]
                if part.inline_data and part.inline_data.data:
                    audio_data += part.inline_data.data
                    if part.inline_data.mime_type:
                        mime_type = part.inline_data.mime_type

        if not audio_data:
            return jsonify({"error": "No audio generated"}), 500

        wav_data = _convert_to_wav(audio_data, mime_type)
        return Response(wav_data, mimetype="audio/wav",
                        headers={"Cache-Control": "no-cache"})

    except Exception as e:
        print(f"[Gemini TTS Preview] Error: {e}")
        return jsonify({"error": str(e)}), 500


# =====================================================================
# /api/transliterate
# =====================================================================
@router.post("/api/transliterate")
async def transliterate(req: Request):
    body = await adapt_body(req)
    return to_response(_transliterate(FlaskReq(req, body)))


def _transliterate(request):
    """Use Gemini to convert Urdu script text to Roman Urdu."""
    data = request.get_json()
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"result": ""})
    settings = load_settings()
    prompt = ("Convert the following Urdu text to Roman Urdu (Urdu written in English/Latin letters).\n"
              "Keep the meaning exactly the same. Do NOT translate to English — just transliterate to Roman Urdu.\n"
              "If the text is already in English or Roman Urdu, return it unchanged.\n\n"
              f"Text: {text}\n\nRoman Urdu:")
    try:
        client = get_genai_client(settings)
        model = settings.get("gemini_model", "gemini-2.5-flash")
        response = client.models.generate_content(model=model, contents=prompt)
        return jsonify({"result": response.text.strip()})
    except Exception as e:
        return jsonify({"result": text, "error": str(e)})



@router.post("/api/voice/session")
def voice_session() -> Dict[str, Any]:
    """Return everything the browser needs to connect to Gemini Live API."""
    from voice_agent import _build_system_instruction, _build_tools

    _live_model_cache = voice_session._cache  # type: ignore[attr-defined]
    settings = load_settings()
    api_key = settings.get("gemini_api_key", "")
    voice = settings.get("gemini_tts_voice", "Leda")

    if not api_key:
        raise HTTPException(status_code=500, detail="No Gemini API key configured.")

    # Discover the right live model (cached per process)
    if _live_model_cache.get("model"):
        model = _live_model_cache["model"]
    else:
        preferred = [
            "models/gemini-2.5-flash-live-preview",
            "models/gemini-2.0-flash-live-001",
            "models/gemini-3.1-flash-live-preview",
        ]
        model = preferred[0]
        try:
            r = http_requests.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
                timeout=10,
            )
            if r.status_code == 200:
                all_models = [m.get("name", "") for m in r.json().get("models", [])]
                model = next((p for p in preferred if p in all_models),
                             next((m for m in all_models if "live" in m.lower()), preferred[0]))
        except Exception as e:
            logger.warning("Live model probe failed: %s", e)
        _live_model_cache["model"] = model

    logger.info("Voice session — model=%s, voice=%s", model, voice)
    return {
        "apiKey": api_key,
        "model": model,
        "voice": voice,
        "systemInstruction": _build_system_instruction(settings),
        "tools": _build_tools(),
    }
voice_session._cache = {"model": None}  # type: ignore[attr-defined]


# =====================================================================
# /api/voice/test
# =====================================================================
@router.post("/api/voice/test")
def voice_test() -> Dict[str, Any]:
    settings = load_settings()
    api_key = settings.get("gemini_api_key", "")
    if not api_key:
        return {"ok": False, "error": "No API key configured"}
    try:
        r = http_requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
            timeout=10,
        )
        if r.status_code != 200:
            return {"ok": False, "error": f"API returned {r.status_code}: {r.text[:300]}"}
        models_data = r.json()
        all_models = [m.get("name", "") for m in models_data.get("models", [])]
        return {
            "ok": True,
            "live_models":  [m for m in all_models if "live" in m.lower()],
            "flash_models": [m for m in all_models if "flash" in m.lower() and ("2.5" in m or "2.0" in m)][:10],
            "total_models": len(all_models),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# =====================================================================
# /api/voice/query — BigQuery SQL execution for voice tool calls
# =====================================================================
@router.post("/api/voice/query")
async def voice_query(req: Request) -> Dict[str, Any]:
    from google.cloud import bigquery
    body = await req.json()
    sql = (body.get("sql") or "").strip()

    if not sql:
        return {"result": "No SQL provided."}

    sql_upper = sql.upper().strip()
    if not sql_upper.startswith("SELECT"):
        return {"result": "Error: Only SELECT queries are allowed."}

    dangerous = ["INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE", "MERGE"]
    for word in dangerous:
        if word in sql_upper.split():
            return {"result": f"Error: {word} statements are not allowed."}

    settings = load_settings()
    logger.info("Voice query — SQL: %s", sql[:200])
    try:
        client = bigquery.Client(project=settings["gcp_project"])
        df = client.query(sql).to_dataframe()
        if len(df) == 0:
            result = "No records found matching this query."
        elif len(df) <= 30:
            result = df.to_string(index=False)
        else:
            result = df.head(30).to_string(index=False) + f"\n... ({len(df)} total rows, showing first 30)"
        logger.info("Voice query — returned %d rows", len(df))
        return {"result": result}
    except Exception as e:
        logger.error("Voice query failed: %s", e)
        return {"result": f"Query error: {e}"}
