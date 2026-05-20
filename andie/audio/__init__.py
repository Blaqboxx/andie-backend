"""andie.audio — cognitive audio sensor infrastructure.

Modules:
  events/   — audio event bus (ring buffer, subscribe/push)
  asr/      — automatic speech recognition (faster-whisper)
  tts/      — text-to-speech synthesis (edge-tts)
  ssml/     — SSML directive parser
  prompts/  — named audio prompt registry
  wakewords/— wake word + keyword detection
  streaming/— WebSocket audio chunk streaming pipeline
  router    — FastAPI /audio/* endpoints
"""
