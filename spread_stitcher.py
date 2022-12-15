#!/bin/python3
import shutil
import tempfile
from pathlib import Path
from typing import List
from PIL import Image
from sys import stderr

# FIRST PAGE MESSAGE CONFIG
# This script currently assumes R to L mangas and generates it such that the
# last page is the front of the PDF. By default, a page with this message will
# be placed as the front page of the PDF.
go_to_back_text = "This manga is read Right to Left! Go to the last page :)"
# Font used to print the message
font_ttf = "LiberationSans-Regular.ttf"
font_size = 40


def extract(cbz: Path, out: Path) -> List[Path] | str:
    """
    Extract the given cbz to out and verify the pages of the cbz are the same size.
    Will insert blank pages if needed to guarantee the cbz returns an even
    number of pages so each page has a spread partner.
    If successful, returns list of paths to all images.
    If there's an error, returns an error string.
    """
    if not out.is_dir():
        return f"[{cbz.name}] ERROR: {out} is not a directory!"

    if not cbz.exists():
        return f"[{cbz.name}] ERROR: {cbz} is not a valid path! Skipping to next file"

    if cbz.suffix != ".cbz":
        return f"[{cbz.name}] ERROR: {cbz} is not a cbz! Skipping to next file"

    shutil.unpack_archive(cbz, out, "zip")

    # In reverse, because we want right to left.
    imgs = sorted(out.iterdir(), reverse=True)

    # Ensure all images have same dimensions
    with Image.open(imgs[0]) as first_img:
        width = first_img.width
        height = first_img.height
        mode = first_img.mode

    for img in imgs:
        with Image.open(img) as curr_page:
            # I've found that if a page is smaller than the dimensions, it's
            # almost never an issue and is only off by a few pixels. However,
            # if it's *larger*, then this cbz may already handle spreads!
            if curr_page.width > width or curr_page.height > height:
                return (f"[{cbz.name}] ERROR: "
                        f"{img} {curr_page.width}x{curr_page.height} "
                        f"doesn't match {width}x{height}! Skipping...")

    if len(imgs) % 2 != 0:
        # We have an odd amount of images. Add a blank page to the front of
        # the imgs array to add a blank page at the end of the chapter so every
        # page has a spread partner.
        with Image.new(mode, (width, height)) as blank:
            blank.paste("white", box=(0, 0, width, height))
            location = out / ("blank" + imgs[0].suffix)
            blank.save(location)
            imgs.insert(0, location)

    assert len(imgs) % 2 == 0

    return imgs


def convert(cbz: Path, del_old_cbz: bool = False, skip_warning_page: bool = False, quiet: bool = False) -> bool:
    """Converts a cbz in-place to have merged pages."""
    with tempfile.TemporaryDirectory() as tmpdir:
        if not quiet:
            print(f"[{cbz.name}] Starting...")

        WORKDIR = Path(tmpdir)
        ARCHIVEDIR = WORKDIR / "archive"
        OUTDIR = WORKDIR / "out"

        ARCHIVEDIR.mkdir()
        extract_out = extract(cbz, ARCHIVEDIR)
        if isinstance(extract_out, str):
            print(extract_out, file=stderr)
            return False

        imgs = extract_out

        # get width, height, mode
        with Image.open(imgs[0]) as first_img:
            width = first_img.width
            height = first_img.height
            mode = first_img.mode

        # Second: stitch pages together
        OUTDIR.mkdir()
        count = 1

        # Add page to remind me to go to the last page, and avoid spoilers by
        # not immediately showing the last page of the chapter.
        # Help from https://stackoverflow.com/questions/16373425/add-text-on-image-using-pil
        # and https://stackoverflow.com/questions/1970807/center-middle-align-text-with-pil
        # If we only have 2 pages, don't insert - we only have one page to show!
        if not skip_warning_page and len(imgs) > 2:
            from PIL import ImageDraw, ImageFont
            with Image.new(mode, (width*2, height)) as img:
                img.paste("white", box=(0, 0, width*2, height))
                draw = ImageDraw.Draw(img)
                large_font = ImageFont.truetype(font_ttf, size=font_size)
                (_, _, text_width, text_height) = draw.textbbox(
                    (0, 0), go_to_back_text, font=large_font)
                draw.text(((width * 2 - text_width)/2, (height - text_height)/2),
                          go_to_back_text, font=large_font, fill="black")
                img.save(OUTDIR / f"{count:03d}.png")
                count += 1

        # The list is already in reverse. Pop off 2 images, stick first one
        # on left, second on right, give it the right name, done
        # Made with help from https://stackoverflow.com/questions/10657383/stitching-photos-together
        while len(imgs) >= 2:
            with (Image.open(imgs.pop(0)) as img1,
                  Image.open(imgs.pop(0)) as img2,
                  Image.new(img1.mode, (width*2, height)) as out):
                out.paste(im=img1, box=(0, 0))
                out.paste(im=img2, box=(width, 0))
                out.save(OUTDIR / f"{count:03d}.png")
                count += 1

        assert len(imgs) == 0

        # Third: rewrite cbz
        shutil.make_archive(str(cbz), "zip", OUTDIR)

        # Move old cbz if needed, otherwise will be overwritten
        if not del_old_cbz:
            shutil.move(cbz, cbz.with_stem(cbz.stem + "_original"))
        # make_archive adds a .zip to the end of the name, remove the .zip
        # Overwrites existing .cbz if del_old_cbz is true
        shutil.move(cbz.with_name(cbz.name + ".zip"), cbz)

        if not quiet:
            print(f"[{cbz.name}] Done!")
        return True


def main():
    import argparse
    from functools import partial
    from multiprocessing import Pool
    parser = argparse.ArgumentParser(
        description='Correctly show manga spreads by stitching / merging / combining the pages of a cbz.')
    parser.add_argument('cbzs', metavar='CBZ', type=str,
                        nargs='+', help='cbzs to stitch')
    parser.add_argument('-d', '--del-old-cbzs', dest='del_old_cbz', action='store_true',
                        default=False, help="Delete the original, unstitched cbzs instead of saving them")
    parser.add_argument('-w', '--skip-warning-page', dest='skip_warning_page', action='store_true', default=False,
                        help="Do not put a warning page at the beginning of the cbz telling you the manga starts on the last page")
    parser.add_argument('-q', '--quiet', dest='quiet', action='store_true', default=False,
                        help="Do not print status updates. Errors will still be printed.")

    args = parser.parse_args()

    with Pool() as p:
        results = p.map(partial(convert, del_old_cbz=args.del_old_cbz,
                                skip_warning_page=args.skip_warning_page, quiet=args.quiet), map(Path, args.cbzs))

    if not all(results):
        exit(1)


if __name__ == '__main__':
    main()
