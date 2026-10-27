"""Link snapping: near-misses on masked links are corrected, nothing else is touched."""

from __future__ import annotations

from veil.frame import Detection, snap_to_link

LINK = Detection("claim_number", (36, 268, 138, 284), "guard", "ID_1", link=True)
TEXT = Detection("person_name", (290, 268, 410, 284), "guard", "NAME_1", link=False)
FACE = Detection("face", (500, 200, 600, 300), "sam", None, link=True)


def test_click_just_below_masked_link_snaps_onto_it():
    # The real failure: Spark clicked y=296 under a link spanning y 268-284.
    assert snap_to_link([LINK, TEXT], 87, 296) == (87, 276)


def test_click_on_link_is_untouched():
    assert snap_to_link([LINK], 87, 276) is None
    assert snap_to_link([LINK], 87, 286) is None  # inside the padded box


def test_far_or_sideways_clicks_are_untouched():
    assert snap_to_link([LINK], 87, 320) is None   # too far below
    assert snap_to_link([LINK], 200, 290) is None  # not under the link horizontally


def test_plain_text_and_visual_regions_never_attract_clicks():
    assert snap_to_link([TEXT], 350, 292) is None
    assert snap_to_link([FACE], 550, 305) is None
