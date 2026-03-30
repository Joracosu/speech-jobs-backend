# Third-Party Components

This document is a short public reference for external components and resources that are central to understanding, running, or evaluating this repository. It is intentionally non-exhaustive.

## Libraries / frameworks

- **FastAPI**  
  Official link: https://fastapi.tiangolo.com/  
  License: MIT  
  Used here for the public HTTP API surface and application bootstrap.

- **SQLAlchemy + Alembic**  
  Official links: https://www.sqlalchemy.org/ and https://alembic.sqlalchemy.org/  
  License: MIT  
  Used here for persistence models, database access, and schema migrations.

- **faster-whisper**  
  Official link: https://github.com/SYSTRAN/faster-whisper  
  Used here as the worker-side ASR engine.

- **pyannote.audio**  
  Official link: https://github.com/pyannote/pyannote-audio  
  Used here as the optional worker-side diarization stack.

- **PyTorch / torchaudio**  
  Official links: https://pytorch.org/ and https://pytorch.org/audio/stable/  
  Used here as part of the optional GPU and diarization runtime path.

## Models

- **Whisper model family** (`base`, `small`, `medium` as resolved by the public processing profiles)  
  Official link: https://github.com/openai/whisper  
  License: MIT  
  Used here for transcription through the `faster-whisper` runtime.

- **`pyannote/speaker-diarization-community-1`**  
  Official link: https://huggingface.co/pyannote/speaker-diarization-community-1  
  Used here as the configured optional diarization model path. Access depends on `HUGGINGFACE_TOKEN` and the model access granted to that account.

## Demo audio / resources

- **`examples/audio/monologue_james_6m20s.m4a`**  
  Used here as the canonical quickstart/demo audio referenced in `README.md`.

- **`examples/audio/conversation_two_speakers_10m.m4a`**  
  Used here as an additional local example resource for multi-speaker-oriented validation.

Detailed provenance and intended usage for example media are intentionally left for the future `examples/README.md`.

## Visual assets

The repository now includes one dedicated public visual asset: `docs/assets/architecture-overview.png`, used in `README.md` for the architecture overview. This section remains intentionally brief and does not try to become a broader asset catalog.
