"""
AI image detector core — pure: no Discord, no database, no Redis.

``formats``   sniffs the image type and whether a file can carry C2PA data;
``manifest``  turns a *verified* C2PA manifest store into a verdict (AI-generated,
              partially AI, authenticated photo, retouched photo) plus the few
              privacy-safe facts the cards display;
``png_text``  spots unsigned generation parameters (Stable Diffusion, ComfyUI)
              in PNG text chunks;
``verify``    runs c2pa-python on the bytes (synchronous — call it in a thread)
              with the official trust list and the allowed EKUs.

The Discord side (download, module, cards) lives in
``services/ai_detector_service.py``, ``modules/ai_detector.py`` and
``utils/ai_detector_views.py``. See docs/features/AI_DETECTOR.md.
"""
