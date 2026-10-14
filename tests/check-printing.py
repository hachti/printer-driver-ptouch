#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise generated PPDs and the complete filters without a printer backend."""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import tempfile
import zlib
import xml.etree.ElementTree as ET


OPTIONS = ("DitherMode", "MinFeatureSize", "ScreenFrequency", "ScreenAngle",
           "ToneGamma", "ToneBrightness", "ToneContrast", "RotatePage")
NEUTRAL = dict(DitherMode="Screen", RotatePage="None", ToneGamma="Neutral",
               ToneBrightness="Neutral", ToneContrast="Neutral")


def run(command, **kwargs):
    result = subprocess.run(command, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, **kwargs)
    if result.returncode:
        raise AssertionError("Command failed: " + shlex.join(map(str, command))
                             + "\n" + result.stderr.decode(errors="replace"))
    return result.stdout


def default(ppd, name):
    match = re.search(r"^\*Default" + name + r": (\S+)", ppd, re.M)
    return match.group(1) if match else None


def png(width=9, height=3, depth=1, resolution=True, interlace=0):
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data)))
    result = b"\x89PNG\r\n\x1a\n"
    result += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth,
                                        0, 0, 0, interlace))
    if resolution:
        result += chunk(b"pHYs", struct.pack(">IIB", 14173, 28346, 1))
    row = b"\x55\x80" if depth == 1 else bytes(width)
    result += chunk(b"IDAT", zlib.compress((b"\0" + row) * height))
    return result + chunk(b"IEND", b"")


def read_raster(build, data):
    result = run([str(build / "raster-info")], input=data)
    pages = [json.loads(line) for line in result.splitlines()]
    for page in pages:
        page["data"] = bytes.fromhex(page["data"])
    return pages


def pixel(page, x, y):
    if not 0 <= x < page["width"] or not 0 <= y < page["height"]:
        return 0
    return bool(page["data"][y * page["stride"] + x // 8]
                & (0x80 >> (x % 8)))


def coverage(page):
    return sum(pixel(page, x, y) for y in range(page["height"])
               for x in range(page["width"]))


def dark_patch(page):
    x0, x1 = [round(x * page["xdpi"] / 72) for x in (67, 83)]
    y0, y1 = [round(y * page["ydpi"] / 72) for y in (66, 82)]
    return sum(pixel(page, x, y) for y in range(y0, y1)
               for x in range(x0, x1))


def black_near(page, x, y):
    return pixel(page, round(x * page["xdpi"] / 72),
                 round(y * page["ydpi"] / 72))


def check_bridge(build):
    bridge = str(build / "ptouch-raster-bridge")
    pages = read_raster(build, run([bridge], input=png() + png()))
    assert len(pages) == 2
    for page in pages:
        assert (page["width"], page["height"]) == (9, 3)
        assert (page["xdpi"], page["ydpi"]) == (360, 720)
        assert page["data"] == b"\xaa\0" * 3
    for bad in (b"not raster", png()[:-20], png(depth=8),
                png(resolution=False), png(interlace=1)):
        result = subprocess.run([bridge], input=bad, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        assert result.returncode == 1, "Malformed PNG was accepted or crashed"
        assert b"AddressSanitizer" not in result.stderr
        assert b"runtime error:" not in result.stderr
    assert run([bridge], input=b"") == b""
    print("Bridge: multi-page PNG, polarity, padding, resolution and errors pass")


def document(directory):
    ps = directory / "test.ps"
    ps.write_text("""%!PS-Adobe-3.0
%%Pages: 1
<< /PageSize [170.08 102.05] >> setpagedevice
0 setgray 12 66 14 20 rectfill
0.5 setgray 60 12 90 70 rectfill
0.25 setgray 65 18 20 20 rectfill
0.75 setgray 100 18 20 20 rectfill
showpage
""")
    pdf = directory / "test.pdf"
    run(["gs", "-q", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pdfwrite",
         "-sOutputFile=" + str(pdf), str(ps)])
    return pdf


def filter_job(build, directory, ppd, pdf, options, name):
    text = ppd.read_text()
    pattern = r'^\*FoomaticRIPCommandLine: "(.*?)"\n\*End'
    match = re.search(pattern, text, re.S | re.M)
    assert match, ppd
    command = match.group(1).replace("&&\n", "")
    raster = directory / (name + ".raster")
    command = command.replace("ptouch-raster-bridge",
                              shlex.quote(str(build / "ptouch-raster-bridge")))
    command = command.replace("rastertoptch",
                              "tee " + shlex.quote(str(raster)) + " | "
                              + shlex.quote(str(build / "rastertoptch")))
    command = "&&\n".join(command[i:i + 180]
                           for i in range(0, len(command), 180))
    replacement = '*FoomaticRIPCommandLine: "' + command + '"\n*End'
    text = text[:match.start()] + replacement + text[match.end():]
    prepared = directory / (name + ".ppd")
    prepared.write_text(text)
    argv = ["cupsfilter", "-p", str(prepared), "-m", "printer/ptouch-test", "-e"]
    opts = {"PageSize": "Custom.170.08x102.05", "landscape": "False",
            "MirrorPrint": "False", "RequireMatchingLabelSize": "False"}
    opts.update(options)
    for key, value in opts.items():
        argv += ["-o", key + "=" + value]
    result = subprocess.run(argv + [str(pdf)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    (directory / (name + ".log")).write_bytes(result.stderr)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert b"ERROR:" not in result.stderr, result.stderr.decode(errors="replace")
    assert raster.exists(), result.stderr.decode(errors="replace")
    return result.stdout, read_raster(build, raster.read_bytes()), raster.read_bytes()


def check_ppds(build, directory, baseline):
    ppds = sorted((build / "generated/ppds").glob("*.ppd"))
    assert ppds, "Test PPD generation produced no files"
    pdf = document(directory)
    for ppd in ppds:
        text = ppd.read_text()
        run(["cupstestppd", "-W", "all", str(ppd)])
        pt = "-ptouch-pt.ppd" in ppd.name
        for option in OPTIONS:
            assert (f"*OpenUI *{option}/" in text) == pt, (ppd, option)
        if pt:
            special = "PT-P950NW-" in ppd.name
            assert default(text, "RotatePage") == ("CCW" if special else "None")
            assert default(text, "DitherMode") == ("ErrorDiffusion" if special else "Screen")
            assert default(text, "ToneGamma") == ("G140" if special else "Neutral")
            assert default(text, "MirrorPrint") == "False"
        if baseline:
            old = baseline / ppd.name
            assert old.exists(), old
            if not pt:
                assert text == old.read_text(), "QL PPD changed: " + ppd.name
            before, _, _ = filter_job(build, directory, old, pdf, {}, "old-" + ppd.stem)
            after, _, _ = filter_job(build, directory, ppd, pdf, NEUTRAL, "new-" + ppd.stem)
            assert before == after, "Native output changed: " + ppd.name
    print(f"PPDs: {len(ppds)} models, defaults and syntax pass"
          + ("; baseline printer data identical" if baseline else ""))
    return pdf


def check_quality(build, directory, pdf):
    ppd = build / "generated/ppds/Brother-PT-P950NW-ptouch-pt.ppd"
    neutral = dict(NEUTRAL, DitherMode="ErrorDiffusion")
    _, pages, raster = filter_job(build, directory, ppd, pdf, neutral, "neutral")
    reference = coverage(pages[0])
    reference_patch = dark_patch(pages[0])
    assert run([str(build / "ptouch-raster-bridge")], input=raster) == raster
    choices = {
        "ToneGamma": ["G070", "G085", "G120", "G140", "G180", "G220"],
        "ToneBrightness": ["Minus20", "Minus10", "Plus10", "Plus20", "Plus30"],
        "ToneContrast": ["C050", "C075", "C125", "C150", "C200"],
        "MinFeatureSize": ["2"],
    }
    for option, values in choices.items():
        for value in values:
            _, result, _ = filter_job(build, directory, ppd, pdf,
                                      dict(neutral, **{option: value}), option + value)
            count = coverage(result[0])
            if option == "ToneGamma":
                assert (count < reference) == (int(value[1:]) > 100), value
            elif option == "ToneBrightness":
                assert (count < reference) == value.startswith("Plus"), value
            elif option == "ToneContrast":
                assert ((dark_patch(result[0]) > reference_patch)
                        == (int(value[1:]) > 100)), value
            elif option == "MinFeatureSize":
                assert count > reference
    filter_job(build, directory, ppd, pdf,
               dict(neutral, ToneGamma="G140", ToneBrightness="Plus10",
                    ToneContrast="C150"), "combined")
    for mode in ("Screen", "ErrorDiffusion"):
        for resolution, ydpi in (("360x180dpi", 180), ("360dpi", 360),
                                 ("360x720dpi", 720)):
            for turn in ("None", "CW", "CCW"):
                opts = dict(neutral, DitherMode=mode, Resolution=resolution,
                            RotatePage=turn)
                _, result, _ = filter_job(build, directory, ppd, pdf, opts,
                                          mode + resolution + turn)
                page = result[0]
                assert len(result) == 1
                assert (page["xdpi"], page["ydpi"]) == (360, ydpi)
                w, h = (170.08, 102.05) if turn == "None" else (102.05, 170.08)
                assert abs(page["width"] - w * 360 / 72) <= 1
                assert abs(page["height"] - h * ydpi / 72) <= 1
                # A solid marker is near the original upper left corner.
                # Probe its transformed location and an unmarked opposite corner.
                if turn == "None":
                    black, white = (19, 26.05), (151, 26.05)
                elif turn == "CW":
                    black, white = (76, 19), (26.05, 19)
                else:
                    black, white = (26.05, 151.08), (76, 151.08)
                assert black_near(page, *black), (mode, resolution, turn, "marker")
                assert not black_near(page, *white), (mode, resolution, turn, "mirror")
    for option, prefix, values in (
            ("ScreenFrequency", "", ["20lpi", "30lpi", "45lpi", "60lpi", "75lpi", "85lpi"]),
            ("ScreenAngle", "Deg", ["0", "15", "30", "45", "60", "75", "90"])):
        patterns = set()
        for value in values:
            _, result, _ = filter_job(build, directory, ppd, pdf,
                                      dict(NEUTRAL, **{option: prefix + value}),
                                      option + value)
            patterns.add(result[0]["data"])
        assert len(patterns) >= 5, (option, "screen setting had no effect")
    # Exercise the model defaults independently of explicit job settings.
    _, result, _ = filter_job(build, directory, ppd, pdf, {}, "defaults")
    assert (result[0]["width"], result[0]["height"]) == (510, 850)
    # A normal multi-page PDF must survive every intermediate filter too.
    two = directory / "two-pages.pdf"
    run(["gs", "-q", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pdfwrite",
         "-sOutputFile=" + str(two), str(pdf), str(pdf)])
    for mode in ("Screen", "ErrorDiffusion"):
        _, result, _ = filter_job(build, directory, ppd, two,
                                  {"DitherMode": mode}, "two-" + mode)
        assert len(result) == 2
        assert all((p["width"], p["height"]) == (510, 850) for p in result)
    print("Quality: tone controls, screens, resolutions, rotation and polarity pass")


def check_page_changes(build):
    ppd = (build / "generated/ppds/Brother-PT-P950NW-ptouch-pt.ppd").read_text()

    def code(option, value):
        match = re.search(r'^\*' + option + ' ' + value
                          + r'/[^:]+: "(.*?)"\n\*End', ppd, re.M | re.S)
        assert match, (option, value)
        return match.group(1).replace("&&\n", "")

    sizes = ((60, 90), (62, 92), (90, 60))
    for turn in ("CW", "CCW"):
        for device in ("cups", "pngmonod"):
            counts = []
            for gamma in (False, True):
                ps = "/pt_test_hooks 0 def\n"
                ps += "<< /PageSize [60 90] /cupsColorSpace 3 " \
                      "/cupsBitsPerColor 1 /BeginPage " \
                      "{ pop /pt_test_hooks pt_test_hooks 1 add def } " \
                      ">> setpagedevice\n"
                ps += code("RotatePage", turn) + "\n"
                if gamma:
                    ps += code("ToneGamma", "G140") + "\n"
                for width, height in sizes:
                    ps += "/pt_test_hooks 0 def\n"
                    ps += f"<< /PageSize [{width} {height}] /BeginPage " \
                          "{ pop /pt_test_hooks pt_test_hooks 1 add def } " \
                          ">> setpagedevice\n"
                    ps += "/pt_test_hooks 0 def 0 currentpagedevice " \
                          "/BeginPage get exec\n"
                    ps += "pt_test_hooks 1 ne { (Lost BeginPage hook) = 1 .quit } if\n"
                    # Document code sees logical dimensions, even after rotation.
                    ps += (f"currentpagedevice /PageSize get dup 0 get {width} "
                           f"ne exch 1 get {height} ne or "
                           "{ (Bad logical paper size) = 1 .quit } if\n")
                    # A hook-only device change must preserve both hook and turn.
                    ps += "<< /BeginPage { pop /pt_test_hooks " \
                          "pt_test_hooks 1 add def } >> setpagedevice\n"
                    # Copying the reported device dictionary must not rewrap hooks.
                    ps += "currentpagedevice setpagedevice\n"
                    # Poppler emits such transfer resets in page content.
                    ps += "{} settransfer 0.5 setgray 10 10 20 20 rectfill showpage\n"
                rendered = run(["gs", "-q", "-dBATCH", "-dNOPAUSE", "-r360",
                                "-sDEVICE=" + device, "-sOutputFile=%stdout", "-"],
                               input=ps.encode())
                raster = run([str(build / "ptouch-raster-bridge")], input=rendered)
                pages = read_raster(build, raster)
                assert len(pages) == len(sizes)
                for page, (width, height) in zip(pages, sizes):
                    assert (page["width"], page["height"]) == (height * 5, width * 5)
                counts.append([coverage(page) for page in pages])
            assert all(light < dark for dark, light in zip(*counts)), counts
    print("Pages: size changes below 5 points and document transfer resets pass")


def prepare_ppds(build):
    generator = os.environ.get("FOOMATIC_PPDFILE", "foomatic-ppdfile")
    assert shutil.which(generator), "foomatic-db-engine is required for tests"
    output = build / "generated/ppds"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ptouch-test-db-") as directory:
        database = Path(directory)
        source = database / "db/source"
        source.mkdir(parents=True)
        (database / "db/oldprinterids").write_text(
            "# Test database uses named printer IDs.\n")
        for kind in ("driver", "printer", "opt"):
            shutil.copytree(build / "generated" / kind, source / kind)
        environment = dict(os.environ, FOOMATICDB=str(database))
        for printer in sorted((source / "printer").glob("*.xml")):
            root = ET.parse(printer).getroot()
            name = root.attrib["id"].split("/", 1)[-1]
            driver = root.findtext("driver")
            assert name and driver, printer
            ppd = run([generator, "-p", name, "-d", driver], env=environment)
            assert ppd.startswith(b"*PPD-Adobe:"), (name, driver)
            (output / (name + "-" + driver + ".ppd")).write_bytes(ppd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--builddir", type=Path, required=True)
    parser.add_argument("--baseline-ppds", type=Path)
    parser.add_argument("--keep", type=Path, help="retain rasters and filter logs")
    args = parser.parse_args()
    for program in ("gs", "cupsfilter", "cupstestppd", "tee"):
        if not shutil.which(program):
            parser.error(program + " is required for offline printing checks")
    build = args.builddir.resolve()
    prepare_ppds(build)
    def check(directory):
        check_bridge(build)
        pdf = check_ppds(build, directory, args.baseline_ppds)
        check_quality(build, directory, pdf)
        check_page_changes(build)
    if args.keep:
        args.keep.mkdir(parents=True, exist_ok=True)
        check(args.keep.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="ptouch-check-") as directory:
            check(Path(directory))


if __name__ == "__main__":
    main()
