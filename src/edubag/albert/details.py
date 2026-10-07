"""Turn a labeled field on an Albert (PeopleSoft) page into class-detail keys.

Shared by the live-page scraper (:class:`edubag.albert.client.AlbertClient`)
and the saved-HTML roster parser (:class:`edubag.albert.roster.AlbertRoster`),
so both produce the same keys.
"""

import re

# Map PeopleSoft field names that have no visible label to friendlier keys.
LABEL_MAPPINGS = {
    "derived_clsrch_descr200": "full_course_name",
    "derived_clsrch_descrlong": "description",
    "ssr_cls_dtl_wrk_ssr_cls_txb_msg": "textbook_message",
}


def normalize_label(label: str) -> str:
    """Convert a label to snake_case variable name format."""
    return re.sub(r"[^\w]+", "_", label.lower()).strip("_")


def clean_value(value_text: str | None) -> str | None:
    """Collapse whitespace (including non-breaking spaces); None stays None."""
    if value_text is None:
        return None
    return re.sub(r"\s+", " ", value_text.replace("\xa0", " ")).strip()


def label_from_parent_id(label_parent_id: str | None) -> str | None:
    """Recover a field name from an unlabeled field's ``win0div<FIELD>lbl`` id."""
    if not label_parent_id:
        return None
    match = re.search(r"win0div([A-Z0-9_]+)lbl", label_parent_id)
    return match.group(1) if match else None


def parse_detail_field(label_text: str | None, label_parent_id: str | None, value_text: str | None) -> dict:
    """Class-detail entries for one labeled field on an Albert page.

    Args:
        label_text: Text of the field's ``.ps-label`` (may be empty or nbsp).
        label_parent_id: id of the label's parent element, used when the
            label is blank (e.g. ``win0divDERIVED_SSR_FC_DESCR254lbl``).
        value_text: Text of the field's ``.ps_box-value``.

    Returns:
        A dict of normalized key(s) to value; empty if the field has no
        usable label or value.
    """
    label = (label_text or "").strip()
    if not label or label == "\xa0":
        label = label_from_parent_id(label_parent_id) or ""
    value_text = clean_value(value_text)
    if not label or not value_text:
        return {}

    key = normalize_label(label)
    key = LABEL_MAPPINGS.get(key, key)

    # Convert clean integer strings, but keep leading zeros (e.g. section "016").
    value: int | str
    if value_text.startswith("0") and len(value_text) > 1:
        value = value_text
    else:
        try:
            value = int(value_text)
        except ValueError:
            value = value_text

    # "Course Name (class_number) (class_type)"
    if key == "derived_ssr_fc_descr254" and isinstance(value, str):
        match = re.match(r"^(.+?)\s*\(([^)]+)\)\s*\(([^)]+)\)$", value)
        if match:
            return {
                "course_name": match.group(1).strip(),
                "class_number": int(n) if (n := match.group(2).strip()).isdigit() else n,
                "class_type": match.group(3).strip(),
            }

    # "School | Term | Type"
    if key == "derived_clsrch_sss_page_keydescr" and isinstance(value, str):
        return {"school": value.split("|")[0].strip()}

    return {key: value}
