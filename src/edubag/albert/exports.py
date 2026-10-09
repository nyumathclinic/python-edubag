"""Export an :class:`~edubag.albert.roster.AlbertRoster` to vCards and Anki decks."""

import hashlib
from pathlib import Path

import genanki
import pandas as pd
import vobject

from edubag.albert.roster import AlbertRoster

ANKI_MODEL_NAME = "edubag Albert roster"


def _stable_id(key: str) -> int:
    """A deterministic Anki model/deck id (Anki wants a positive 31-bit-ish int)."""
    return (1 << 30) + int(hashlib.sha1(key.encode()).hexdigest()[:7], 16)


def _value(record: dict, key: str) -> str:
    value = record.get(key)
    return "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)


def course_label(roster: AlbertRoster) -> str:
    """Human-readable section label, e.g. "MATH-UA 122 - 016, Fall 2026".

    Falls back to the course name (e.g. "Calculus II - 016, Fall 2026") when
    the roster has no subject code, as with the HTML roster page.
    """
    c = roster.course
    if c.get("Subject Code"):
        course = f"{c['Subject Code']} {c.get('Catalog Number', '')}".strip()
    else:
        course = str(c.get("course_name", ""))
    section = c.get("Section", "")
    term = c.get("Semester", "")
    name = " - ".join(str(part) for part in (course, section) if part)
    return ", ".join(part for part in (name, term) if part)


def student_vcard(roster: AlbertRoster, record: dict) -> vobject.base.Component:
    """Build one student's vCard, following ps2vcard's field layout."""
    card = vobject.vCard()
    first, last = _value(record, "first"), _value(record, "last")
    card.add("n").value = vobject.vcard.Name(family=last, given=first)
    card.add("fn").value = " ".join(part for part in (first, last) if part) or _value(record, "name")
    if _value(record, "email"):
        email = card.add("email")
        email.value = _value(record, "email")
        email.type_param = "INTERNET"
    card.add("title").value = "Student"
    org = roster.course.get("org") or "New York University"
    program, plan = _value(record, "program"), _value(record, "plan")
    card.add("org").value = [org, program] if program else [org]
    if program:
        card.add("x-nyu-progplan").value = " - ".join(part for part in (program, plan) if part)
    if _value(record, "campus_id"):
        card.add("x-nyu-nnumber").value = _value(record, "campus_id")
    # Apple Contacts shows "Related Names"; use one for the course section.
    card.add("item1.x-ablabel").value = "course"
    card.add("item1.x-abrelatednames").value = course_label(roster)
    photo = roster.photo_path(record.get("photo"))
    if photo is not None and photo.exists():
        p = card.add("photo")
        p.value = photo.read_bytes()
        p.encoding_param = "b"
        p.type_param = "PNG" if photo.suffix.lower() == ".png" else "JPEG"
    return card


def write_vcards(roster: AlbertRoster, path: Path) -> Path:
    """Write every student in the roster to a single ``.vcf`` file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cards = [student_vcard(roster, r) for r in roster.students.to_dict(orient="records")]
    path.write_text("".join(card.serialize() for card in cards))
    return path


def anki_model() -> genanki.Model:
    """Note type: photo on the front, name and details on the back."""
    return genanki.Model(
        _stable_id(ANKI_MODEL_NAME),
        ANKI_MODEL_NAME,
        fields=[
            {"name": "NetID"},
            {"name": "Photo"},
            {"name": "Name"},
            {"name": "Program"},
            {"name": "Section"},
        ],
        templates=[
            {
                "name": "Photo → Name",
                "qfmt": "{{Photo}}",
                "afmt": '{{FrontSide}}<hr id="answer"><div class="name">{{Name}}</div>'
                '<div class="detail">{{Program}}</div><div class="detail">{{Section}}</div>',
            }
        ],
        # Albert's roster photos are ~60x87 px thumbnails; scale them up to a
        # fixed share of the screen (max-height alone would never enlarge them).
        css=".card { font-family: sans-serif; text-align: center; }"
        " .card img { height: 50vh; width: auto; max-width: 90vw; object-fit: contain; }"
        " .name { font-size: 1.6em; } .detail { color: #666; }",
        sort_field_index=2,
    )


def write_anki_deck(roster: AlbertRoster, path: Path, deck_name: str | None = None) -> Path:
    """Write an Anki ``.apkg`` flashcard deck (photo → name) for the roster.

    Students without a photo are skipped. Deck, model and note ids are derived
    from the section and NetID, so re-importing a newer roster updates cards
    instead of duplicating them.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    label = course_label(roster)
    deck = genanki.Deck(_stable_id(f"deck:{roster.pathstem}"), deck_name or f"Roster::{label}")
    model = anki_model()
    media = []
    for record in roster.students.to_dict(orient="records"):
        photo = roster.photo_path(record.get("photo"))
        if photo is None or not photo.exists():
            continue
        # Anki's media folder is flat, so file names must be unique across decks.
        key = _value(record, "netid") or _value(record, "campus_id")
        media_name = f"edubag_{key}{photo.suffix or '.jpg'}"
        staged = path.parent / f".{path.stem}_media" / media_name
        staged.parent.mkdir(exist_ok=True)
        staged.write_bytes(photo.read_bytes())
        media.append(str(staged))
        name = " ".join(p for p in (_value(record, "first"), _value(record, "last")) if p) or _value(record, "name")
        deck.add_note(
            genanki.Note(
                model=model,
                fields=[key, f'<img src="{media_name}">', name, _value(record, "program"), label],
                guid=genanki.guid_for("edubag-albert", roster.pathstem, key),
            )
        )
    package = genanki.Package(deck)
    package.media_files = media
    package.write_to_file(str(path))
    for staged in media:
        Path(staged).unlink()
    if media:
        Path(media[0]).parent.rmdir()
    return path
