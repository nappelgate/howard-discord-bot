#!/usr/bin/env python3
"""
TTS generator with optional RVC voice conversion pipeline.

Modes (auto-detected from args):
  1. RVC only   -- --rvc-model + --input  (converts existing audio file)
  2. XTTS + RVC -- --text + --rvc-model   (synthesises then converts)
  3. XTTS only  -- --text, no --rvc-model (synthesises with voice cloning)
  4. edge-tts   -- fallback when no XTTS voice profiles exist

Usage examples:
    # Synthesise with XTTS voice cloning:
    python generate_tts.py --text "Hello" --voice gilbert --out out.wav

    # Synthesise with edge-tts then convert with RVC:
    python generate_tts.py --text "Hello" --rvc-model gilbert --out out.wav

    # Convert an existing audio file through RVC:
    python generate_tts.py --input existing.wav --rvc-model gilbert --out out.wav
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("COQUI_TOS_AGREED", "1")

_HERE = Path(__file__).parent
_VOICES_DIR = _HERE / "data" / "voices"
_RVC_DIR = _HERE / "data" / "voices" / "rvc"
_RVC_SCRIPT = _HERE / "rvc_infer.py"
_RVC_PYTHON = _HERE / ".venv-rvc" / "bin" / "python"
_XTTS_MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"


def resolve_xtts_speaker(voice: str | None, speaker: str | None) -> Path | None:
    if speaker:
        p = Path(speaker)
        return p if p.exists() else None
    if voice:
        p = _VOICES_DIR / f"{voice}.wav"
        return p if p.exists() else None
    profiles = sorted(_VOICES_DIR.glob("*.wav"))
    return profiles[0] if profiles else None


def rvc_available() -> bool:
    return _RVC_PYTHON.exists() and _RVC_SCRIPT.exists()


def rvc_model_exists(model: str) -> bool:
    return (_RVC_DIR / f"{model}.pth").exists()


def run_rvc(input_path: str, output_path: str, model: str, pitch: int = 0) -> None:
    result = subprocess.run(
        [str(_RVC_PYTHON), str(_RVC_SCRIPT),
         "--input", input_path,
         "--out", output_path,
         "--model", model,
         "--pitch", str(pitch)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(result.stderr[-500:], file=sys.stderr)
        sys.exit(result.returncode)


def list_voices() -> list[str]:
    voices = []
    if _VOICES_DIR.exists():
        voices += sorted(p.stem for p in _VOICES_DIR.glob("*.wav"))
    if _RVC_DIR.exists():
        voices += sorted(f"rvc:{p.stem}" for p in _RVC_DIR.glob("*.pth"))
    return voices


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", help="Text to synthesise")
    parser.add_argument("--input", help="Existing audio file to convert (skips TTS)")
    parser.add_argument("--voice", help="XTTS voice profile name (data/voices/<name>.wav)")
    parser.add_argument("--speaker", help="Explicit path to XTTS speaker WAV")
    parser.add_argument("--rvc-model", dest="rvc_model", help="RVC model name (data/voices/rvc/<name>.pth)")
    parser.add_argument("--pitch", type=int, default=0, help="RVC pitch shift in semitones")
    parser.add_argument("--out", required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument("--list-voices", action="store_true", dest="list_voices")
    args = parser.parse_args()

    if args.list_voices:
        for v in list_voices():
            print(v)
        return

    if not args.text and not args.input:
        print("ERROR: provide --text or --input", file=sys.stderr)
        sys.exit(1)

    use_rvc = args.rvc_model and rvc_available()

    # --- Step 1: produce base audio ---
    if args.input:
        base_audio = args.input
        owns_base = False
    elif use_rvc:
        # RVC mode: use fast edge-tts for the words, RVC for the voice
        import asyncio, edge_tts
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        base_audio = tmp.name
        owns_base = True
        async def _tts():
            comm = edge_tts.Communicate(args.text, voice="en-US-GuyNeural")
            await comm.save(base_audio)
        asyncio.run(_tts())
    else:
        # XTTS mode
        speaker = resolve_xtts_speaker(args.voice, args.speaker)
        if speaker is None:
            # final fallback: edge-tts
            import asyncio, edge_tts
            async def _tts():
                comm = edge_tts.Communicate(args.text, voice="en-US-ChristopherNeural")
                await comm.save(args.out)
            asyncio.run(_tts())
            return
        from TTS.api import TTS
        tts = TTS(model_name=_XTTS_MODEL, progress_bar=False)
        tts.tts_to_file(
            text=args.text,
            speaker_wav=str(speaker),
            language=args.language,
            file_path=args.out,
        )
        return

    # --- Step 2: RVC conversion ---
    if use_rvc:
        run_rvc(base_audio, args.out, args.rvc_model, args.pitch)
        if owns_base:
            Path(base_audio).unlink(missing_ok=True)
    else:
        # No RVC: base audio IS the output
        import shutil
        shutil.move(base_audio, args.out)


if __name__ == "__main__":
    main()
