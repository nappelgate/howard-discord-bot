#!/usr/bin/env python3
"""
RVC voice conversion via Applio's inference API.
Called as a subprocess by generate_tts.py after TTS generation.

Usage:
    python rvc_infer.py --input speech.wav --model gilbert --out converted.wav
    python rvc_infer.py --input speech.wav --pth data/voices/rvc/gilbert.pth --out converted.wav
"""
import argparse
import os
import sys
from pathlib import Path

_APPLIO_DIR = Path(__file__).parent / "applio"
_RVC_DIR = Path(__file__).parent / "data" / "voices" / "rvc"

sys.path.insert(0, str(_APPLIO_DIR))
os.chdir(_APPLIO_DIR)


def resolve_model(model: str | None, pth: str | None) -> tuple[str, str]:
    if pth:
        pth_path = Path(pth)
        index_path = pth_path.with_suffix(".index")
        return str(pth_path), str(index_path) if index_path.exists() else ""
    if model:
        pth_path = _RVC_DIR / f"{model}.pth"
        index_path = _RVC_DIR / f"{model}.index"
        if not pth_path.exists():
            print(f"ERROR: RVC model not found: {pth_path}", file=sys.stderr)
            sys.exit(1)
        return str(pth_path), str(index_path) if index_path.exists() else ""
    # default: first available model
    models = sorted(_RVC_DIR.glob("*.pth"))
    if not models:
        print("ERROR: no RVC models found in data/voices/rvc/", file=sys.stderr)
        sys.exit(1)
    pth_path = models[0]
    index_path = pth_path.with_suffix(".index")
    return str(pth_path), str(index_path) if index_path.exists() else ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Input WAV file")
    parser.add_argument("--out", required=True, help="Output WAV file")
    parser.add_argument("--model", help="Named RVC model (stem of data/voices/rvc/<name>.pth)")
    parser.add_argument("--pth", help="Explicit path to .pth model file")
    parser.add_argument("--pitch", type=int, default=0, help="Pitch shift in semitones")
    parser.add_argument("--index-rate", type=float, default=0.75)
    parser.add_argument("--f0-method", default="rmvpe")
    args = parser.parse_args()

    pth_path, index_path = resolve_model(args.model, args.pth)

    from core import run_infer_script
    run_infer_script(
        pitch=args.pitch,
        index_rate=args.index_rate,
        volume_envelope=1.0,
        protect=0.33,
        f0_method=args.f0_method,
        input_path=args.input,
        output_path=args.out,
        pth_path=pth_path,
        index_path=index_path,
        split_audio=False,
        f0_autotune=False,
        f0_autotune_strength=1.0,
        proposed_pitch=False,
        proposed_pitch_threshold=155.0,
        clean_audio=True,
        clean_strength=0.5,
        export_format="WAV",
        embedder_model="contentvec",
        embedder_model_custom=None,
        formant_shifting=False,
        formant_qfrency=1.0,
        formant_timbre=1.0,
        post_process=False,
        reverb=False,
        pitch_shift=False,
        limiter=False,
        gain=False,
        distortion=False,
        chorus=False,
        bitcrush=False,
        clipping=False,
        compressor=False,
        delay=False,
    )


if __name__ == "__main__":
    main()
