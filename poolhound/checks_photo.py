"""The photo tracer's camera metadata, and the parser that reads it."""

import io
import json
import os
import shutil
import struct
import subprocess
import tempfile

from .selftest import check


def _apple_makernote(x, y, z, order=b"MM", tag=0x0008, typ=10, count=3):
    """An Apple MakerNote block carrying one acceleration vector.

    Built rather than borrowed. The alternative is a real iPhone photograph in
    the repository, which is somebody's house, somebody's GPS fix and a
    megabyte of it — for three numbers. This fixture is checked against
    exiftool below, so it is not merely self-consistent: the same bytes are
    read the same way by the reference implementation.
    """
    end = ">" if order == b"MM" else "<"
    hdr = b"Apple iOS\x00" + b"\x00\x01" + order
    n = 1
    val_off = len(hdr) + 2 + 12 * n + 4
    e = struct.pack(end + "HHII", tag, typ, count, val_off)
    if typ == 10:
        vals = b"".join(struct.pack(end + "ii", int(round(v * 1000000)), 1000000)
                        for v in (x, y, z))
    elif typ == 5:
        vals = b"".join(struct.pack(end + "II", int(round(abs(v) * 1000000)), 1000000)
                        for v in (x, y, z))
    else:
        # A type the parser must refuse. What the payload says is irrelevant —
        # it never gets that far — so it is filler of the right length.
        vals = b"\x00" * 24
    return hdr + struct.pack(end + "H", n) + e + struct.pack(end + "I", 0) + vals


def _photo(path, gravity=(0.0, -0.5, -0.86), focal35=24, mn=None):
    from PIL import Image
    im = Image.new("RGB", (4032, 3024), (30, 80, 140))
    ex = Image.Exif()
    ex[0x010F], ex[0x0110] = "Apple", "iPhone 15 Pro"
    sub = ex.get_ifd(0x8769)
    sub[0x927C] = _apple_makernote(*gravity) if mn is None else mn
    sub[0xA405] = focal35
    im.save(path, "JPEG", exif=ex.tobytes())


def t_the_camera_tilt_is_read_without_a_host_binary():
    """Apple writes the accelerometer into the photo; the server could not read it.

    tilt_from_gravity() turns that vector into the pitch and roll the
    ground-plane homography needs, so nobody has to type them. But it lives in
    the MakerNote, and only the exiftool path read one — and exiftool is not in
    the deploy image and is not going to be, because it brings perl with it.
    So the automatic tilt had never run on the machine that serves every
    visitor. It was documented as "a degradation, not a failure", and in
    production the degradation was total.
    """
    from . import pool_shape as ps
    print("\n  photo — the camera tilt, read without exiftool")

    d = tempfile.mkdtemp()
    p = os.path.join(d, "fixture.jpg")
    _photo(p)

    meta = ps._exif_via_pillow(p)
    check("the model still comes through", meta.get("Model"), "iPhone 15 Pro")
    check("and the focal length", meta.get("FocalLengthIn35mmFormat"), 24)
    got = [round(float(v), 3) for v in (meta.get("AccelerationVector") or "").split()]
    check("and the acceleration vector, with no host binary",
          got, [0.0, -0.5, -0.86])

    # The whole point of reading it: a tilt nobody typed.
    t = ps.tilt_from_gravity(*got) if got else None
    check("which becomes a camera tilt", t is not None and round(t["tilt_deg"]), 60)

    # THE REFERENCE IMPLEMENTATION AGREES, where it is installed. This is what
    # makes the fixture evidence rather than a mirror: exiftool parses the same
    # bytes independently, and a disagreement means the format was misread.
    if shutil.which("exiftool"):
        r = subprocess.run(["exiftool", "-s3", "-n", "-AccelerationVector", p],
                           capture_output=True, text=True, timeout=25)
        ref = [round(float(v), 3) for v in r.stdout.replace(",", " ").split()]
        check("exiftool reads the same three numbers from the same bytes",
              ref, got)
    else:
        print("      (exiftool absent here — the cross-check did not run)")


def t_a_hostile_maker_note_is_refused_not_guessed():
    """/api/photo is public, so these bytes come from a stranger.

    Every length is checked against the buffer before it is used, and anything
    not completely understood returns None rather than a guess: a wrong tilt is
    worse than no tilt, because no tilt asks the reader for one and a wrong one
    silently skews every distance taken off the photograph.
    """
    from . import pool_shape as ps
    print("\n  photo — a MakerNote from a stranger")

    good = _apple_makernote(0.0, -0.5, -0.86)
    check("the good one reads", [round(v, 3) for v in ps.apple_acceleration(good)],
          [0.0, -0.5, -0.86])

    cases = {
        "not bytes at all": "Apple iOS\x00 and then some",
        "empty": b"",
        "truncated header": b"Apple iOS\x00\x00\x01",
        "another vendor": b"Nikon\x00\x00\x01MM" + good[14:],
        "a byte order it does not know": _apple_makernote(0, 0, -1, order=b"XX"),
        "a count that runs off the end": good[:14] + struct.pack(">H", 4000) + good[16:],
        "an offset past the end": (good[:14 + 2]
                                  + struct.pack(">HHII", 0x0008, 10, 3, 0xFFFF)
                                  + good[14 + 2 + 12:]),
        "the wrong number of components": _apple_makernote(0, 0, -1, count=2),
        "a type it does not accept": _apple_makernote(0, 0, -1, typ=3),
        "truncated mid-value": good[:-6],
    }
    for why, blob in cases.items():
        check(f"refused: {why}", ps.apple_acceleration(blob), None)

    # A zero denominator is a division, and a division is how a parser crashes.
    zero = bytearray(good)
    struct.pack_into(">i", zero, len(good) - 4, 0)
    check("refused: a zero denominator", ps.apple_acceleration(bytes(zero)), None)

    # Little-endian is accepted, because the format says so even if Apple
    # writes big-endian today.
    check("and a little-endian block still reads",
          [round(v, 3) for v in
           (ps.apple_acceleration(_apple_makernote(0.0, -0.5, -0.86, order=b"II")) or [])],
          [0.0, -0.5, -0.86])


def t_a_photo_with_no_maker_note_degrades_rather_than_breaks():
    """Most photographs are not from an iPhone, and the tracer still works on
    them — the reader is asked for the tilt instead."""
    from . import pool_shape as ps
    from PIL import Image
    print("\n  photo — no MakerNote is not an error")

    d = tempfile.mkdtemp()
    p = os.path.join(d, "plain.jpg")
    Image.new("RGB", (800, 600), (200, 200, 200)).save(p, "JPEG")
    meta = ps._exif_via_pillow(p)
    check("the size is still read", (meta.get("ImageWidth"), meta.get("ImageHeight")),
          (800, 600))
    check("and no tilt is invented", meta.get("AccelerationVector"), None)
    check("a file that is not an image returns nothing at all",
          ps._exif_via_pillow(os.path.join(d, "nope.jpg")), {})


def t_the_camera_fields_are_read_from_the_original():
    """The converted copy has no metadata, so it must not be what is read.

    HEIC is what an iPhone writes by default. It is converted before anything
    else happens, and the converter the server uses — _pillow_to_jpeg — saves
    without an `exif=` argument, so the copy carries nothing. Reading the
    camera fields off that copy meant no model, no focal length and no tilt for
    the commonest kind of photograph there is.

    Asserted by watching which PATH each reader is handed, because the
    filenames are the whole question: original first, converted only as a
    fallback for the sips route where Pillow cannot open the original.
    """
    import os
    import tempfile
    from . import pool_shape as ps
    print("\n  photo — the camera fields come from the original")

    d = tempfile.mkdtemp()
    src = os.path.join(d, "fixture.jpg")
    _photo(src)

    seen = []
    real_pillow, real_exiftool = ps._exif_via_pillow, ps._exif_via_exiftool

    def spy_pillow(path):
        seen.append(("pillow", os.path.basename(path)))
        return real_pillow(path)

    def spy_exiftool(path):
        seen.append(("exiftool", os.path.basename(path)))
        return real_exiftool(path)

    ps._exif_via_pillow, ps._exif_via_exiftool = spy_pillow, spy_exiftool
    try:
        out, err = ps.prepare_photo(open(src, "rb").read())
    finally:
        ps._exif_via_pillow, ps._exif_via_exiftool = real_pillow, real_exiftool

    check("the photo was accepted", err, None)
    check("the tilt survived the round trip", out and out.get("tilt_deg"), 59.8)
    check("and the model", out and out.get("model"), "Apple iPhone 15 Pro")
    # THE FIRST FILE ANY READER IS POINTED AT IS THE UPLOAD ITSELF.
    # prepare_photo writes the posted bytes to "in.bin" and names its
    # derivatives "conv.jpg" (the HEIC conversion) and "small.jpg" (the
    # resize). Naming them here couples this to an internal detail on purpose:
    # WHICH of the three is read is the entire question, and the distinction is
    # invisible from the outside because all three are the same picture.
    first = seen[0][1] if seen else None
    check("the first file read is the upload, not a derivative", first, "in.bin")
    check("and neither derivative is read before it",
          [n for _, n in seen[:1] if n in ("conv.jpg", "small.jpg")], [])


# An accelerometer reading from a real iPhone photograph of a real pool, taken
# in landscape from an upstairs window. Three numbers, which identify nothing.
# Kept because the failure it exposes only appears on a LANDSCAPE photograph,
# and every synthetic fixture written before it happened to be portrait.
_REAL_LANDSCAPE = (-0.8605535036, 0.02622615548, -0.4826886356)


def t_the_roll_is_measured_in_the_pictures_axes():
    """Apple reports the accelerometer in the HANDSET's frame; the photograph
    has since been turned upright.

    The EXIF orientation tag is the record of how far it was turned, and
    reading the vector without applying it leaves the roll out by exactly that
    much. For a pool that is ninety degrees, because nobody photographs a pool
    in portrait — so every real photograph took the branch nothing had tested.

    Measured on the photograph this came from: the device-frame roll is -91.7
    and the horizon in the picture is dead level. The tilt was right all along;
    it comes from the Z component alone and rotation about the lens axis does
    not touch it.

    This matters more than the numbers suggest. The page shows the tilt in a
    box the reader can correct, and applies the roll without ever showing it —
    so a wrong roll is the one value here that goes wrong in silence.
    """
    from . import pool_shape as ps
    print("\n  photo — the roll belongs to the picture, not the handset")

    land = ps.tilt_from_gravity(*_REAL_LANDSCAPE, orientation=1)
    check("a landscape photo rolls by a degree or two",
          abs(land["roll_deg"]) < 5, True)
    check("and its tilt is read from the Z component",
          round(land["tilt_deg"], 1), 29.3)

    # The defect, stated: the same vector read in the old frame.
    old = ps.tilt_from_gravity(*_REAL_LANDSCAPE, orientation=6)
    check("read in the handset's frame it is ninety degrees out",
          round(old["roll_deg"]), -92)
    check("which the tilt does not notice",
          round(old["tilt_deg"], 1), round(land["tilt_deg"], 1))

    # The old default is preserved for anything that does not pass one.
    check("the default frame is the one this used to assume",
          ps.tilt_from_gravity(*_REAL_LANDSCAPE)["roll_deg"], old["roll_deg"])

    # Known orientations, level phone in each: no roll in any of them.
    for o, vec in ((1, (-1.0, 0.0, 0.0)), (3, (1.0, 0.0, 0.0)),
                   (6, (0.0, -1.0, 0.0)), (8, (0.0, 1.0, 0.0))):
        check(f"orientation {o}: a level phone has no roll",
              round(ps.tilt_from_gravity(*vec, orientation=o)["roll_deg"]), 0)

    # A mirrored or unknown orientation is not guessed at.
    unknown = ps.tilt_from_gravity(*_REAL_LANDSCAPE, orientation=5)
    check("an orientation it does not know gives no roll",
          unknown["roll_deg"], 0.0)
    check("but still gives the tilt, which does not depend on it",
          round(unknown["tilt_deg"], 1), 29.3)


def t_a_landscape_photo_resolves_to_a_ground_plane():
    """The end of the chain, and the reason the roll matters.

    With the handset-frame roll the ground homography for this photograph
    refused to solve at all — ninety degrees of roll puts most of the frame
    above the horizon, where rays never meet the water. That refusal was the
    system failing safe; a smaller error would have passed and quietly skewed
    every distance taken off the picture.
    """
    from . import pool_shape as ps
    print("\n  photo — and it reaches a ground plane")

    t = ps.tilt_from_gravity(*_REAL_LANDSCAPE, orientation=1)
    ok = ps.camera_ground_points(1600, 900, 21.0, height_ft=12.0,
                                 tilt_deg=t["tilt_deg"], roll_deg=t["roll_deg"])
    check("the corrected roll solves the ground plane", ok is not None, True)

    bad = ps.tilt_from_gravity(*_REAL_LANDSCAPE, orientation=6)
    no = ps.camera_ground_points(1600, 900, 21.0, height_ft=12.0,
                                 tilt_deg=bad["tilt_deg"], roll_deg=bad["roll_deg"])
    check("and the handset-frame one did not", no is None, True)


def t_a_decompression_bomb_is_refused_before_it_is_decoded():
    """Bytes are not pixels, and /api/photo is a public POST.

    The upload was bounded at 60 MB on the wire and not at all on the decode.
    A PNG of 225 million white pixels is 246 KB compressed, passes that cap,
    and expands to roughly 675 MB of RGB inside a container whose mem_limit is
    512 MB — one request, one OOM kill, from anyone on the internet. Pillow's
    own bomb check only WARNS below 179 Mpx, so it does not stop it either.

    The guard reads the dimensions out of the file header with no imaging
    library, which is the only way to refuse a decode without performing it.
    """
    import struct
    import zlib
    from . import pool_shape
    print("\n  photo — a decompression bomb is refused before anything decodes")

    def bomb(w, h):
        def chunk(tag, data):
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
        ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
        raw = b"".join(b"\x00" + b"\xff" * w for _ in range(h))
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))

    data = bomb(15000, 15000)
    check("the bomb is small enough to pass the byte cap",
          len(data) < 60 * 1024 * 1024, True)
    res, err = pool_shape.prepare_photo(data)
    check("but it is refused", res is None and bool(err), True)
    check("and the message says why, in pixels", "pixels" in (err or ""), True)
    check("the ceiling is below what the container can hold",
          pool_shape.MAX_PIXELS * 3 < 512 * 1024 * 1024, True)
    # A photo any phone actually produces must still be accepted — a guard that
    # refuses the real case is the same defect in the other direction.
    ok, ok_err = pool_shape.prepare_photo(bomb(2400, 1600))
    check("an ordinary 2400x1600 photo is still accepted", ok_err, None)


def t_a_refill_cost_reads_the_bill_the_way_it_is_written():
    """The same price, in the three units a utility actually bills in.

    Nobody has a price per gallon in front of them. A US water bill quotes
    per CCF — a hundred cubic feet, 748 gallons — or per 1,000 gallons. Ask
    for "the rate per gallon" and the number to hand is 4.50, so a field with
    no unit beside it is a field that reports a 20,000 gallon refill as
    $90,000 and looks like it worked. The three units are checked against each
    other because being wrong by a factor of a thousand is the failure mode
    here, not an arithmetic slip.
    """
    from . import pool_shape as ps
    print("\n  refill — the rate is read in the unit the bill uses")

    # $5.00 per 1,000 gallons IS $0.005 per gallon. Same pool, same money.
    a = ps.refill_cost(20000, 0.005, "gal")
    b = ps.refill_cost(20000, 5.00, "kgal")
    check("per-gallon and per-1,000-gallon agree on the same price",
          (round(a, 6), round(b, 6)), (100.0, 100.0))

    ccf = ps.refill_cost(20000, 4.50, "ccf")
    check("a per-CCF rate divides by 748, not by 1,000",
          round(ccf, 2), round(20000 / ps.GALLONS_PER_CCF * 4.50, 2))

    # THE CONVERSION IS DERIVED, NOT TYPED. 748.052 written out here would be
    # a second definition of what a cubic foot holds.
    check("a CCF is a hundred cubic feet of the one definition",
          round(ps.GALLONS_PER_CCF, 6),
          round(100 * ps.GALLONS_PER_CUBIC_FOOT, 6))

    # NOTHING TO SAY IS NOT ZERO. A confident $0.00 over an empty rate field
    # is a figure somebody could act on.
    check("no volume yet is None, not 0", ps.refill_cost(0, 0.005, "gal"), None)
    check("no rate yet is None", ps.refill_cost(20000, None, "gal"), None)
    check("a blank rate is None", ps.refill_cost(20000, "", "gal"), None)
    check("an unknown unit is None, not a guess",
          ps.refill_cost(20000, 4.50, "litres"), None)
    check("a negative rate is refused", ps.refill_cost(20000, -1, "gal"), None)
    # A ZERO RATE IS A REAL ANSWER -- a well, or water included in a fee.
    check("a rate of zero is an answer, not a missing one",
          ps.refill_cost(20000, 0, "gal"), 0.0)


def t_the_page_gets_the_rate_units_from_the_one_place_that_defines_them():
    """The browser divides by the same number the Python would.

    A CCF written as 748 in the page script is the second copy of a
    conversion that already has an owner, and the copy in a template string
    is the one no test reaches. The units travel as a JSON block, the same way
    the outlines and the chemical catalogue do.
    """
    from . import panels, pool_shape as ps, render
    print("\n  refill — the units are handed over, not retyped")

    units = json.loads(panels.refill_units_json())
    check("every declared unit is exported",
          [u["key"] for u in units], [k for k, _, _ in ps.REFILL_RATE_UNITS])
    by = {u["key"]: u["gallons"] for u in units}
    check("with the gallons each one holds",
          (by.get("gal"), by.get("kgal"), round(by.get("ccf", 0), 6)),
          (1.0, 1000.0, round(ps.GALLONS_PER_CCF, 6)))

    # AND THE PAGE CARRIES IT. A block the script reads by id is a block that
    # has to be in the markup under that id. The JSON block is in TEMPLATE;
    # the figure is in the panel TEMPLATE interpolates, which is why this
    # asks each of them for its own half rather than one of them for both.
    check("the page carries the block the script reads",
          'id="refill-units"' in render.TEMPLATE, True)
    panel = panels.volume_panel(public=True)
    check("and the cost figure it writes into",
          'data-t="cost"' in panel, True)
    check("with a rate field and its unit",
          ('id="vol-rate"' in panel, 'id="vol-rate-unit"' in panel),
          (True, True))
    # PUBLIC TOO. The calculator is the public half of this product, and a
    # cost that only the owner can see would be the one reader who already
    # knows the number.
    check("the rate field is in the public build as well",
          'id="vol-rate"' in panels.volume_panel(public=True), True)
    # THE WHOLE TEMPLATE, not a window around the block.
    #
    # The first version searched only what followed the last mention of
    # "refill-units", and the UNITS array it was written to police sits a few
    # lines ABOVE that mention — so the region was wrong and the breaker that
    # hardcodes the conversion did not make this fail. The meta-check caught
    # it, which is the second time today a detector measured the wrong place.
    # There is nothing else in the page that wants these digits, so the
    # assertion is simply that they are not in it.
    check("the CCF conversion is nowhere in the page script",
          "748" in render.TEMPLATE, False)
