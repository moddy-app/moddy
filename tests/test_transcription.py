"""Voice transcription — attachment detection, guard rails and card rendering.

Everything here is offline: the Discord objects are duck-typed stubs and no
gateway call is ever made.

    pytest tests/test_transcription.py -q
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from discord import ui

from services.transcription_service import (
    ErrorCode,
    MAX_DURATION_SECONDS,
    MAX_FILE_BYTES,
    TranscriptionError,
    TranscriptionResult,
    TranscriptionService,
    attachment_duration,
    find_audio_attachment,
    is_voice_message,
)
from utils.transcription_views import (
    NO_MENTIONS,
    TRANSCRIPT_FILE_NAME,
    build_transcription_message,
    card_locale,
    format_duration,
    format_language,
    render_error_card,
    render_loading_card,
    render_transcription_card,
)


def attachment(filename="voice-message.ogg", content_type="audio/ogg",
               size=100_000, duration=12.0, waveform="AAA"):
    return SimpleNamespace(
        filename=filename, content_type=content_type, size=size,
        duration=duration, waveform=waveform,
    )


def message(attachments=(), voice=True, message_id=1, guild_id=None):
    return SimpleNamespace(
        id=message_id,
        flags=SimpleNamespace(voice=voice),
        attachments=list(attachments),
        guild=SimpleNamespace(id=guild_id) if guild_id else None,
    )


# --------------------------------------------------------------------------- #
# Attachment helpers
# --------------------------------------------------------------------------- #

def test_voice_message_is_detected_by_flag():
    assert is_voice_message(message([attachment()], voice=True))


def test_voice_message_is_detected_by_waveform_when_the_flag_is_absent():
    assert is_voice_message(message([attachment()], voice=False))


def test_a_plain_message_is_not_a_voice_message():
    assert not is_voice_message(message([], voice=False))
    text_file = attachment(filename="notes.txt", content_type="text/plain", waveform=None)
    assert not is_voice_message(message([text_file], voice=False))


def test_audio_is_found_by_content_type_and_by_extension():
    by_type = attachment(filename="whatever", content_type="audio/mpeg")
    assert find_audio_attachment(message([by_type])) is by_type

    by_extension = attachment(filename="recording.M4A", content_type=None)
    assert find_audio_attachment(message([by_extension])) is by_extension


def test_a_non_audio_attachment_is_ignored():
    image = attachment(filename="cat.png", content_type="image/png", waveform=None)
    assert find_audio_attachment(message([image])) is None


def test_duration_is_read_when_present_and_estimated_otherwise():
    assert attachment_duration(attachment(duration=31.5)) == 31.5
    # ~128 kbps fallback: only used to reserve rate-limit budget.
    assert attachment_duration(attachment(duration=None, size=160_000)) == 10.0


# --------------------------------------------------------------------------- #
# Preflight guard rails
# --------------------------------------------------------------------------- #

def service(available=True):
    gateway = SimpleNamespace(
        available=available,
        groq_available=lambda: available,
    )
    return TranscriptionService(SimpleNamespace(gateway=gateway))


def expect_error(callable_, code):
    with pytest.raises(TranscriptionError) as excinfo:
        callable_()
    assert excinfo.value.code == code
    return excinfo.value


def test_preflight_accepts_a_normal_voice_message():
    found = service().preflight(message([attachment()]), requester_id=1)
    assert found.filename == "voice-message.ogg"


def test_preflight_rejects_a_message_without_audio():
    expect_error(
        lambda: service().preflight(message([]), requester_id=1),
        ErrorCode.NO_AUDIO,
    )


def test_preflight_rejects_an_oversized_file_before_any_upload():
    too_big = attachment(size=MAX_FILE_BYTES + 1)
    error = expect_error(
        lambda: service().preflight(message([too_big]), requester_id=1),
        ErrorCode.TOO_LARGE,
    )
    assert error.params["size"] == 25


def test_preflight_rejects_audio_longer_than_the_cap():
    too_long = attachment(duration=MAX_DURATION_SECONDS + 1)
    error = expect_error(
        lambda: service().preflight(message([too_long]), requester_id=1),
        ErrorCode.TOO_LONG,
    )
    assert error.params["minutes"] == 30


def test_preflight_reports_the_feature_as_unavailable_without_groq():
    expect_error(
        lambda: service(available=False).preflight(message([attachment()]), requester_id=1),
        ErrorCode.UNAVAILABLE,
    )


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("seconds,expected", [
    (0, "0:00"), (7, "0:07"), (74.2, "1:14"), (600, "10:00"), (3671, "1:01:11"),
])
def test_duration_formatting(seconds, expected):
    assert format_duration(seconds) == expected


def test_language_names_are_localized_from_whisper_output():
    assert format_language("french", "en-US") == "French"
    assert format_language("fr", "fr") == "Français"
    assert format_language("english", "fr") == "Anglais (US)"


def test_a_public_card_speaks_the_server_language_and_falls_back_in_dms():
    in_guild = SimpleNamespace(guild=SimpleNamespace(preferred_locale="de"), locale="fr")
    assert card_locale(in_guild) == "de"

    in_dm = SimpleNamespace(guild=None, locale="fr")
    assert card_locale(in_dm) == "fr"


def test_an_unknown_language_still_renders():
    assert format_language("klingon", "fr") == "Klingon"
    assert format_language("xx", "fr") == "XX"
    assert format_language(None, "fr") is None


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #

def rendered(view) -> str:
    return " ".join(
        str(getattr(item, "content", "")) for item in view.walk_children()
    )


def test_the_transcription_card_shows_the_text_and_its_metadata():
    view = render_transcription_card(
        text="bonjour tout le monde", locale="fr", duration=12.0,
        language="french", requester_id=42,
    )
    text = rendered(view)
    assert "bonjour tout le monde" in text
    assert "`0:12`" in text
    assert "`Français`" in text
    assert "<@42>" in text


def test_no_transcription_can_ping_anyone():
    """Whatever the model wrote, the card never notifies a soul."""
    assert NO_MENTIONS.everyone is False
    assert NO_MENTIONS.users is False
    assert NO_MENTIONS.roles is False

    view = render_transcription_card(text="@everyone <@1> salut", locale="fr")
    assert "@everyone" in rendered(view)  # kept verbatim, just never resolved


def test_a_truncated_card_says_so_and_points_at_the_attachment():
    view = render_transcription_card(text="…", locale="fr", truncated=True)
    text = rendered(view)
    assert "pièce jointe" in text
    files = [item for item in view.walk_children() if isinstance(item, ui.File)]
    assert [f.media.url for f in files] == [f"attachment://{TRANSCRIPT_FILE_NAME}"]


def test_a_long_transcription_ships_its_full_text_as_a_txt():
    result = TranscriptionResult(
        text="a" * 3500 + "…", full_text="a" * 5000, duration=90.0, truncated=True,
    )
    view, files = build_transcription_message(result, locale="fr", requester_id=7)

    assert len(files) == 1
    assert files[0].filename == TRANSCRIPT_FILE_NAME
    assert files[0].fp.read().decode("utf-8") == "a" * 5000
    # The card still shows the inline preview, so the text is readable at a glance.
    assert "a" * 100 in rendered(view)


def test_a_short_transcription_carries_no_attachment():
    result = TranscriptionResult(text="salut", full_text="salut", duration=2.0)
    view, files = build_transcription_message(result, locale="fr")

    assert files == []
    assert not [item for item in view.walk_children() if isinstance(item, ui.File)]


def test_the_loading_card_uses_the_animated_robot():
    assert "<a:Robot:" in rendered(render_loading_card("fr"))


def test_error_cards_carry_the_matching_message():
    assert "25" in rendered(render_error_card(ErrorCode.TOO_LARGE, "fr", size=25))
    # An unmapped code must still produce a usable card rather than "[key]".
    assert "[" not in rendered(render_error_card("something_new", "fr"))
