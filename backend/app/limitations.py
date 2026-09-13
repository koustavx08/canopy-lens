"""What this tool cannot do, stated in one place.

Every analysis result carries this list. It is deliberately part of the payload
rather than a line in the documentation, because the number that gets pasted
into a report is the one from the API response, and the caveat should travel
with it.

These are the *standing* limits of the method - true of every result this
service produces. Anything specific to one image or one boundary (no
georeferencing, imagery covering half the stand, a repaired polygon) is a
`warning` on that result instead, so the two never have to be told apart by
reading them.
"""

from __future__ import annotations

MODEL_ESTIMATE_NOTICE = (
    "Results are model estimates, not measurements. Crown counts and areas are "
    "approximate and should be verified before use in any decision that matters."
)

LIMITATIONS: tuple[str, ...] = (
    "Overlapping and touching crowns in closed canopy are frequently merged or "
    "split incorrectly.",
    "Dense forest, deep shadow and steep terrain reduce accuracy substantially.",
    "Imagery coarser than roughly 0.5 m per pixel cannot resolve individual "
    "small crowns.",
    "Crowns clipped by the image edge or the boundary are measured only where "
    "visible.",
    "Shrubs, hedges, crops and grass can be detected as trees; the model sees "
    "vegetation, not species.",
    "Models are trained on specific regions and sensors; results shift on "
    "unfamiliar landscapes (domain shift).",
    "A boundary is taken as drawn: the polygon is measured and clipped to, not "
    "checked against where the forest actually is.",
    "Coverage is crown area divided by the analysed area - the ground the "
    "imagery actually covers, which may be smaller than the boundary.",
    "No ground-truth validation is performed. Nothing here is a field-verified "
    "tree inventory.",
)
