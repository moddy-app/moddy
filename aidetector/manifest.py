"""Verified C2PA manifest store -> verdict.

Only ever called on a store whose validation state is ``Trusted`` (the signer
chains to the official C2PA trust list): the classification trusts what the
signer declared, so it must never run on an unverified file.

Classification (``digitalSourceType`` of the actions, active manifest and the
ingredients it was built from):

- ``trainedAlgorithmicMedia`` on the action that *created* the content, with no
  camera capture behind it -> :data:`KIND_AI_FULL`;
- ``compositeWithTrainedAlgorithmicMedia`` anywhere, ``trainedAlgorithmicMedia``
  on a later edit, or an AI creation mixed with a camera capture
  -> :data:`KIND_AI_PARTIAL`;
- ``digitalCapture`` (or ``computationalCapture``, what phones such as the
  Pixel declare) on the creation -> :data:`KIND_PHOTO`, or
  :data:`KIND_PHOTO_EDITED` when it was retouched afterwards without AI.

Ingredients related as ``inputTo`` are skipped: a photo *fed to* a generator
as a reference does not make the generated image a photograph.

Privacy: the store also carries GPS, authors, identity assertions and serial
numbers. Nothing is copied out wholesale — every field the cards may show is
picked here from an explicit whitelist (issuer organisation, tool names, camera
make/model, action names and dates). Anything else never leaves this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

KIND_AI_FULL = "ai_full"
KIND_AI_PARTIAL = "ai_partial"
KIND_PHOTO = "photo"
KIND_PHOTO_EDITED = "photo_edited"
KIND_UNSIGNED = "unsigned"

#: Strongest first — when a message holds several images, the reply is about
#: the one whose verdict ranks highest here.
KIND_PRIORITY = (KIND_AI_FULL, KIND_AI_PARTIAL, KIND_UNSIGNED, KIND_PHOTO_EDITED, KIND_PHOTO)

SOURCE_TRAINED = "trainedAlgorithmicMedia"
SOURCE_COMPOSITE_TRAINED = "compositeWithTrainedAlgorithmicMedia"
SOURCE_CAPTURE = "digitalCapture"
SOURCE_COMPUTATIONAL_CAPTURE = "computationalCapture"
_CAPTURE_SOURCES = (SOURCE_CAPTURE, SOURCE_COMPUTATIONAL_CAPTURE)
_AI_SOURCES = (SOURCE_TRAINED, SOURCE_COMPOSITE_TRAINED)

ACTION_CREATED = "c2pa.created"
#: Actions that count as a retouch of a photograph. Each has an i18n label
#: under ``ai_detector.actions.<name without the c2pa. prefix>``.
EDIT_ACTIONS = (
    "c2pa.edited",
    "c2pa.cropped",
    "c2pa.color_adjustments",
    "c2pa.resized",
    "c2pa.filtered",
    "c2pa.drawing",
    "c2pa.orientation",
    "c2pa.redacted",
    "c2pa.converted",
    "c2pa.transcoded",
    "c2pa.edited.metadata",
)

#: Ingredient relationships whose history belongs to this image.
_HISTORY_RELATIONSHIPS = ("parentOf", "componentOf")
MAX_DEPTH = 16
MAX_ACTIONS = 200

_EXIF_MAKE_KEYS = ("exif:Make", "tiff:Make")
_EXIF_MODEL_KEYS = ("exif:Model", "tiff:Model")


@dataclass
class ActionRecord:
    """One action of the history, reduced to what may be displayed."""

    action: str
    source_type: Optional[str] = None
    when: Optional[datetime] = None
    software: Optional[str] = None
    manifest: str = ""
    depth: int = 0

    @property
    def is_creation(self) -> bool:
        return self.action == ACTION_CREATED

    @property
    def is_ai(self) -> bool:
        return self.source_type in _AI_SOURCES

    @property
    def is_edit(self) -> bool:
        return self.action in EDIT_ACTIONS


@dataclass
class ManifestFacts:
    """Privacy-safe facts about one manifest of the chain."""

    label: str
    issuer: Optional[str] = None
    signature_alg: Optional[str] = None
    signed_at: Optional[datetime] = None
    generator: Optional[str] = None
    generator_version: Optional[str] = None
    device: Optional[str] = None
    depth: int = 0


@dataclass
class C2paVerdict:
    """What the cards say about a verified image."""

    kind: str
    issuer: Optional[str] = None
    tool: Optional[str] = None
    device: Optional[str] = None
    when: Optional[datetime] = None
    #: Retouch action names (``c2pa.cropped``…), de-duplicated, history order.
    edits: List[str] = field(default_factory=list)


@dataclass
class ManifestSummary:
    """Everything the details panel shows. Built from the whitelist only."""

    validation_state: Optional[str]
    manifests: List[ManifestFacts] = field(default_factory=list)
    actions: List[ActionRecord] = field(default_factory=list)
    ingredient_count: int = 0
    claim_version: Optional[int] = None
    verdict: Optional[C2paVerdict] = None

    @property
    def active(self) -> Optional[ManifestFacts]:
        return self.manifests[0] if self.manifests else None


# --------------------------------------------------------------------------- #
# Field helpers
# --------------------------------------------------------------------------- #

def short_source_type(value: Any) -> Optional[str]:
    """``http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture`` -> ``digitalCapture``."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().rstrip("/").rsplit("/", 1)[-1].rsplit("#", 1)[-1] or None


def parse_when(value: Any) -> Optional[datetime]:
    """ISO 8601 (``2026-10-01T12:00:00Z``) -> aware datetime, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00").replace("z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _clean(value: Any, limit: int = 80) -> Optional[str]:
    """A display-safe single line: strings only, trimmed, no markdown breakers."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    for char in ("`", "*", "_", "~", "|", "[", "]", "<", ">", "@", "#"):
        text = text.replace(char, "")
    text = text.strip()
    return text[:limit] or None


def _software_name(agent: Any) -> Optional[str]:
    """``softwareAgent`` is a string in claim v1, ``{"name": …}`` in v2."""
    if isinstance(agent, dict):
        return _clean(agent.get("name"))
    return _clean(agent)


def _generator(manifest: Dict[str, Any]) -> tuple:
    info = manifest.get("claim_generator_info")
    if isinstance(info, list) and info and isinstance(info[0], dict):
        return _clean(info[0].get("name")), _clean(info[0].get("version"), 40)
    if isinstance(info, dict):
        return _clean(info.get("name")), _clean(info.get("version"), 40)
    legacy = manifest.get("claim_generator")
    if isinstance(legacy, str) and legacy.strip():
        # v1 string: "Adobe_Photoshop/25.0 adobe_c2pa/0.7.6 c2pa-rs/0.25.2"
        first = legacy.strip().split()[0]
        name, _, version = first.partition("/")
        return _clean(name.replace("_", " ")), _clean(version, 40)
    return None, None


def _device(manifest: Dict[str, Any]) -> Optional[str]:
    """Camera make + model from the EXIF assertion. Nothing else is read from it
    (it also holds GPS, serial numbers and the owner's name)."""
    for assertion in manifest.get("assertions") or []:
        if not isinstance(assertion, dict):
            continue
        label = str(assertion.get("label") or "")
        if not (label.startswith("stds.exif") or label.startswith("c2pa.metadata")):
            continue
        data = assertion.get("data")
        if not isinstance(data, dict):
            continue
        make = next((_clean(data.get(k), 40) for k in _EXIF_MAKE_KEYS if _clean(data.get(k))), None)
        model = next((_clean(data.get(k), 60) for k in _EXIF_MODEL_KEYS if _clean(data.get(k))), None)
        if model:
            if make and not model.lower().startswith(make.split()[0].lower()):
                return f"{make} {model}"
            return model
        if make:
            return make
    return None


def _actions(manifest: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for assertion in manifest.get("assertions") or []:
        if not isinstance(assertion, dict):
            continue
        if not str(assertion.get("label") or "").startswith("c2pa.actions"):
            continue
        data = assertion.get("data")
        for action in (data.get("actions") if isinstance(data, dict) else None) or []:
            if isinstance(action, dict):
                yield action


def _facts(label: str, manifest: Dict[str, Any], depth: int) -> ManifestFacts:
    signature = manifest.get("signature_info") if isinstance(manifest.get("signature_info"), dict) else {}
    name, version = _generator(manifest)
    return ManifestFacts(
        label=label,
        issuer=_clean(signature.get("issuer")),
        signature_alg=_clean(signature.get("alg"), 20),
        signed_at=parse_when(signature.get("time")),
        generator=name,
        generator_version=version,
        device=_device(manifest),
        depth=depth,
    )


# --------------------------------------------------------------------------- #
# Walk + classification
# --------------------------------------------------------------------------- #

def summarize(store: Dict[str, Any]) -> ManifestSummary:
    """Walk the active manifest and its history ingredients, then classify.

    ``store`` is ``json.loads(reader.json())``. Never raises on odd shapes: a
    field that is missing or of the wrong type is simply not shown.
    """
    summary = ManifestSummary(validation_state=store.get("validation_state"))
    manifests = store.get("manifests")
    active_label = store.get("active_manifest")
    if not isinstance(manifests, dict) or active_label not in manifests:
        return summary

    seen = set()
    pending = [(active_label, 0)]
    while pending:
        label, depth = pending.pop(0)
        if label in seen or depth > MAX_DEPTH:
            continue
        seen.add(label)
        manifest = manifests.get(label)
        if not isinstance(manifest, dict):
            continue
        if depth == 0:
            version = manifest.get("claim_version")
            summary.claim_version = version if isinstance(version, int) else None
        summary.manifests.append(_facts(label, manifest, depth))
        for raw in _actions(manifest):
            if len(summary.actions) >= MAX_ACTIONS:
                break
            summary.actions.append(ActionRecord(
                action=str(raw.get("action") or "")[:60],
                source_type=short_source_type(raw.get("digitalSourceType")),
                when=parse_when(raw.get("when")),
                software=_software_name(raw.get("softwareAgent")),
                manifest=label,
                depth=depth,
            ))
        for ingredient in manifest.get("ingredients") or []:
            if not isinstance(ingredient, dict):
                continue
            summary.ingredient_count += 1
            if ingredient.get("relationship", "componentOf") not in _HISTORY_RELATIONSHIPS:
                continue
            child = ingredient.get("active_manifest")
            if isinstance(child, str) and child in manifests:
                pending.append((child, depth + 1))

    summary.verdict = classify(summary)
    return summary


def _manifest(summary: ManifestSummary, label: str) -> Optional[ManifestFacts]:
    return next((m for m in summary.manifests if m.label == label), None)


def _tool(summary: ManifestSummary, record: Optional[ActionRecord]) -> Optional[str]:
    """``softwareAgent`` of the deciding action, else that manifest's generator."""
    if record is not None and record.software:
        return record.software
    facts = _manifest(summary, record.manifest) if record is not None else summary.active
    if facts is not None and facts.generator:
        return facts.generator
    return summary.active.generator if summary.active else None


def _issuer(summary: ManifestSummary, record: Optional[ActionRecord]) -> Optional[str]:
    facts = _manifest(summary, record.manifest) if record is not None else None
    if facts is not None and facts.issuer:
        return facts.issuer
    return summary.active.issuer if summary.active else None


def _when(summary: ManifestSummary, record: Optional[ActionRecord]) -> Optional[datetime]:
    if record is not None and record.when:
        return record.when
    facts = _manifest(summary, record.manifest) if record is not None else None
    return facts.signed_at if facts is not None else None


def _device_for(summary: ManifestSummary, record: Optional[ActionRecord]) -> Optional[str]:
    facts = _manifest(summary, record.manifest) if record is not None else None
    if facts is not None and facts.device:
        return facts.device
    return next((m.device for m in summary.manifests if m.device), None)


def classify(summary: ManifestSummary) -> Optional[C2paVerdict]:
    """The verdict for a walked store, or None when no rule applies."""
    actions = summary.actions
    ai_creation = next((a for a in actions if a.is_creation and a.source_type == SOURCE_TRAINED), None)
    capture = next((a for a in actions if a.is_creation and a.source_type in _CAPTURE_SOURCES), None)
    ai_edit = next(
        (a for a in actions
         if a.source_type == SOURCE_COMPOSITE_TRAINED
         or (a.source_type == SOURCE_TRAINED and not a.is_creation)),
        None,
    )

    if ai_creation is not None and capture is None:
        return C2paVerdict(
            kind=KIND_AI_FULL,
            issuer=_issuer(summary, ai_creation),
            tool=_tool(summary, ai_creation),
            when=_when(summary, ai_creation),
        )

    if ai_edit is not None or (ai_creation is not None and capture is not None):
        record = ai_edit or ai_creation
        return C2paVerdict(
            kind=KIND_AI_PARTIAL,
            issuer=_issuer(summary, record),
            tool=_tool(summary, record),
            when=_when(summary, record),
        )

    if capture is not None:
        edits: List[str] = []
        first_edit: Optional[ActionRecord] = None
        for record in actions:
            if record.is_edit and not record.is_ai:
                if record.action not in edits:
                    edits.append(record.action)
                first_edit = first_edit or record
        return C2paVerdict(
            kind=KIND_PHOTO_EDITED if edits else KIND_PHOTO,
            issuer=_issuer(summary, capture),
            tool=_tool(summary, first_edit) if first_edit else None,
            device=_device_for(summary, capture),
            when=_when(summary, capture),
            edits=edits,
        )

    return None
