"""The chemical catalogue: the branded products, and doses with no prediction."""

from .selftest import check


def t_the_liquid_stabiliser_matches_its_own_label():
    """Leslie's Instant Conditioner Plus, from the label rather than from a
    chemistry text.

    The label states both "4 ounces added to 10,000 gallons = 1 ppm" and
    "1 gallon raises CYA level by 32 ppm per 10,000 gallons". Those agree —
    128 / 4 = 32 — and checking that they do is the whole reason to quote two
    figures: a third-party summary of this same product gave 35 ppm per gallon
    alongside the same 4 oz per ppm, and the two cannot both be true.
    """
    from . import chemicals as C
    print("\n  catalogue — liquid stabiliser against its label")

    check("4 fl oz in 10,000 gal is 1 ppm",
          round(C.effects("cya_liquid", 4, "floz", 100, 10000)["cya"], 3), 1.0)
    check("and a gallon is 32 ppm",
          round(C.effects("cya_liquid", 1, "gal", 100, 10000)["cya"], 3), 32.0)
    check("the two label figures agree with each other",
          round(C.effects("cya_liquid", 128, "floz", 100, 10000)["cya"], 3),
          round(C.effects("cya_liquid", 1, "gal", 100, 10000)["cya"], 3))

    # It scales with the pool, like everything else here.
    check("and it scales with the volume",
          round(C.effects("cya_liquid", 1, "gal", 100, 20000)["cya"], 2), 16.0)

    # It is a liquid, so the catalogue must refuse a weight.
    check("it is sold as a liquid", C.CHEMICALS["cya_liquid"]["phase"], "liquid")
    check("so pounds are not one of its units",
          "lb" in C.units_for("cya_liquid"), False)

    # The granular kind is still here: they are different products with
    # different behaviour, not two spellings of one.
    check("the granular stabiliser is still separate", "cya" in C.CHEMICALS, True)
    check("and is a solid", C.CHEMICALS["cya"]["phase"], "solid")


def t_a_dose_with_no_prediction_says_so():
    """An empty effect map is NOT an effect of zero.

    A lanthanum phosphate remover has no predicted change, because how much it
    removes depends on how much phosphate is in the water to bind — which this
    product only learns from a lab result days later. The label gives a dose
    RATE and no ppb figure, so inventing one would put a fabricated number in
    front of somebody about to pour something into a pool.

    Rendering the empty map prints nothing, and nothing reads as "this does
    nothing" — the opposite of what the product does. So every consumer states
    the reason instead.
    """
    from . import chemicals as C
    print("\n  catalogue — a dose the product will not predict")

    check("it predicts nothing", C.effects("nophos", 16, "floz", 100, 10000), {})
    why = C.CHEMICALS["nophos"].get("no_effect") or ""
    check("and carries the reason", len(why) > 40, True)
    check("which names what it depends on", "how much is in the water" in why, True)

    # The browser is handed both the empty map and the reason.
    js = C.as_json(10000)
    check("the catalogue exports the reason",
          js["chemicals"]["nophos"]["no_effect"], why)
    check("with an empty per-unit map beside it",
          js["chemicals"]["nophos"]["per_unit"], {})
    # And a chemical that DOES predict carries no such excuse.
    check("a predicting chemical has no reason to give",
          js["chemicals"]["acid"]["no_effect"], "")

    # The page must say it, not render blank.
    from . import render
    check("the page script handles the empty case",
          "no_effect" in render.TEMPLATE and "No estimate" in render.TEMPLATE, True)


def t_every_chemical_can_be_inverted_and_priced():
    """amount_for() is the inverse of effects(), and it is what "what to do
    next" uses to turn a gap into a dose. A chemical that predicts nothing has
    no inverse, and asking for one must not return a confident zero."""
    from . import chemicals as C
    print("\n  catalogue — every entry inverts, or refuses to")

    for key, c in C.CHEMICALS.items():
        one = c["effects"](1.0, c["default_pct"], 1.0, 90.0)
        if not one:
            continue                      # nothing to invert; covered above
        for m in one:
            got = C.amount_for(key, m, one[m] * 3.0, 10000)
            check(f"{key}: three times the effect is three times the dose",
                  got is not None and abs(got - 3.0) < 1e-6, True)

    # The one that predicts nothing is asked, and answers nothing rather than 0.
    check("a chemical with no prediction inverts to nothing",
          C.amount_for("nophos", "phosphates", -500.0, 10000), None)


def t_every_chemical_says_what_it_is_sold_as():
    """The picker names the chemistry AND the bag, and no name serves two.

    MEASURED, as a complaint: "there is no option to add Leslie's Alkalinity
    Up". There was -- it is `bicarb` -- but the picker said "Sodium
    bicarbonate", and the catalogue named the retail product for exactly the
    two entries where somebody had happened to do it. Somebody holding a bag
    read ten chemical names, matched none, and concluded the product could not
    record it. A list that is complete and unrecognisable is a list that is
    not complete to the person reading it.

    THE DISJOINTNESS IS THE DANGEROUS HALF. "pH Up" is soda ash and
    "Alkalinity Up" is bicarbonate; they move different measures by different
    amounts. A shelf name claimed by two entries would hand somebody the wrong
    coefficient with full confidence, which is the one failure mode this
    catalogue must not have -- the same shape as the stabiliser that was
    scaled to a figure its own label contradicted.
    """
    import collections
    from . import chemicals as CH
    print("\n  catalogue — what each chemical is sold as")

    # BOTH DIRECTIONS. A name for a chemical that does not exist is an entry
    # nobody re-reads; a chemical with no name is one nobody can find.
    check("every chemical says what it is sold as",
          sorted(k for k in CH.CHEMICALS if not CH.sold_as(k)), [])
    check("and no name is declared for a chemical that is not there",
          sorted(k for k in CH.SOLD_AS if k not in CH.CHEMICALS), [])

    seen = collections.defaultdict(list)
    for k in CH.CHEMICALS:
        for n in CH.sold_as(k):
            seen[CH._norm(n)].append(k)
    check("no shelf name belongs to two chemicals",
          sorted(f"{n}: {'+'.join(ks)}" for n, ks in seen.items() if len(ks) > 1),
          [])

    # THE ONE THAT WAS ASKED FOR, by name, so this case fails if the mapping
    # that prompted it is ever dropped.
    check("Alkalinity Up resolves to sodium bicarbonate",
          [k for k in CH.CHEMICALS
           if "alkalinity up" in [CH._norm(n) for n in CH.sold_as(k)]],
          ["bicarb"])
    check("and pH Up resolves to soda ash, which is a different chemical",
          [k for k in CH.CHEMICALS
           if "ph up" in [CH._norm(n) for n in CH.sold_as(k)]],
          ["soda_ash"])

    # THE LABEL CARRIES BOTH, and does not repeat itself.
    lab = CH.shelf_label("bicarb")
    check("the picker's text names the chemistry", "Sodium bicarbonate" in lab, True)
    check("and the bag", "Alkalinity Up" in lab, True)
    check("a name already in the label is not repeated",
          CH.shelf_label("nophos").count("NoPHOS"), 1)
    # AND THE BROWSER GETS THE SAME ANSWER, because it renders its own picker.
    # Nested under "chemicals", not keyed at the top level -- as_json also
    # carries gallons, the alkalinity it was computed at, and the effect
    # labels, because the browser needs all four to render a dose.
    j = CH.as_json(15000)["chemicals"]
    check("as_json carries the shelf label",
          j["bicarb"]["shelf_label"], CH.shelf_label("bicarb"))
    check("and the names, for searching",
          "Alkalinity Up" in j["bicarb"]["sold_as"], True)
    check("for every chemical, not just that one",
          sorted(k for k in j if not j[k].get("sold_as")), [])
