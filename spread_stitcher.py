#!/bin/python3
import logging
import shutil
import tempfile
from argparse import Namespace
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path
from typing import List, Tuple

from PIL import Image, ImageColor

# FIRST PAGE MESSAGE CONFIG
# This script currently assumes R to L mangas and generates it such that the
# last page is the front of the PDF. By default, a page with this message will
# be placed as the front page of the PDF.
go_to_back_text = "This manga is read Right to Left! Go to the last page :)"
# Font used to print the message
font_ttf = "arial.ttf"
font_size = 40


class WrongImageSize(Exception):
    pass


logger = logging.getLogger("spread_stitch")


def convert_volume(
    cbzs: List[Path],
    del_old_cbz: bool = False,
    skip_warning_page: bool = False,
    quiet: bool = False,
):
    """Converts a list of cbzs into one single cbz with merged spreads, placed
    in the same folder as the first cbz in the list.

    :raises: FileExistsError if volume file name already exists
    :raises: ChildProcessError if a child extract process throws an exception"""
    assert len(cbzs) > 1

    final_path = cbzs[0].parent / f"{cbzs[0].stem}-{cbzs[-1].name}"

    if final_path.exists():
        raise FileExistsError(
            f"{[final_path.name]} ERROR: file name already exists, stopping. "
            "Delete the file and retry to proceed. (Was this volume already converted?)"
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        logger.info(f"[{final_path.name}] Starting volume...")

        WORKDIR = Path(tmpdir)
        VOLDIR = WORKDIR / "vol"
        VOLDIR.mkdir()

        with ProcessPoolExecutor() as p:
            # reverse the manga order because we want the first chapter to be
            # at the *end* of the cbz, because we read right to left
            # Start from 1 to leave room for warning page
            results = p.map(
                partial(
                    extract_stitch_move, workdir=WORKDIR, voldir=VOLDIR, quiet=quiet
                ),
                enumerate(reversed(cbzs), start=1),
            )

        if not all(results):
            raise ChildProcessError(
                f"[{final_path.name}] Terminating volume since earlier chapters failed."
            )

        # Insert warning page at beginning if needed
        if not skip_warning_page:
            # Get info from random element in VOLDIR
            img_path = next(VOLDIR.iterdir())
            with Image.open(img_path) as img:
                # don't multiply width by 2: the dimensions are from stitched images
                write_warning_page(
                    VOLDIR / f"000_000{img_path.suffix}",
                    img.mode,
                    img.width,
                    img.height,
                )
        create_cbz(VOLDIR, final_path)

        if del_old_cbz:
            for cbz in cbzs:
                cbz.unlink()

        logger.info(
            f"[{final_path.name}] Done with volume! You can find it at {str(final_path)}"
        )


def extract_stitch_move(
    volnum_and_cbz: Tuple[int, Path], workdir: Path, voldir: Path, quiet: bool
) -> bool:
    """Used by convert_volume. Given a tuple of volume num and cbz,
    extract, stitch, and move stitched images to voldir."""
    (volnum, cbz) = volnum_and_cbz
    logger.info(f"\t[{cbz.name}] Starting...")

    ARCHIVEDIR = workdir / f"archive_{cbz.name}"
    ARCHIVEDIR.mkdir()
    try:
        extract_out = extract(cbz, ARCHIVEDIR)
    except (FileNotFoundError, WrongImageSize) as e:
        logger.error(e)
        return False

    imgs = extract_out

    OUTDIR = workdir / f"out_{cbz.name}"
    OUTDIR.mkdir()
    # skip the warning page! We'll manually insert it for the volume later
    stitch(imgs, OUTDIR, skip_warning_page=True)

    for img in OUTDIR.iterdir():
        shutil.move(img, voldir / f"{volnum:03d}_{img.name}")

    logger.info(f"\t[{cbz.name}] Done!")
    return True


def convert(
    cbz: Path,
    del_old_cbz: bool = False,
    skip_warning_page: bool = False,
    quiet: bool = False,
):
    """Converts a cbz in-place to have merged pages.

    :raises: FileExistsError if del_old_cbz is false and for a cbz file a.cbz,
    a_original.cbz is in the same folder; or the file name ends with _original"""
    # Check that we don't have a name conflict for original file or if this is an original chapter
    if not del_old_cbz:
        ORIGINALCBZPATH = cbz.with_stem(f"{cbz.stem}_original")
        if ORIGINALCBZPATH.exists():
            raise FileExistsError(
                f"[{cbz.name}] ERROR: {ORIGINALCBZPATH.name} already exists, "
                f"skipping file. Delete {ORIGINALCBZPATH.name} to proceed with "
                "this file. (Was this chapter already converted?)"
            )

        if cbz.stem.endswith("_original"):
            raise FileExistsError(
                f"[{cbz.name}] ERROR: cbz name ends with _original, skipping "
                "file. Please rename this file to not end in _original if you "
                "wish to convert it. (Was this chapter already converted?)"
            )

    with tempfile.TemporaryDirectory() as tmpdir:
        logger.info(f"[{cbz.name}] Starting...")

        WORKDIR = Path(tmpdir)

        # First, archive and verify cbz file in ARCHIVEDIR
        ARCHIVEDIR = WORKDIR / "archive"
        ARCHIVEDIR.mkdir()
        try:
            extract_out = extract(cbz, ARCHIVEDIR)
        except (FileNotFoundError, WrongImageSize) as e:
            logger.error(e)
            return

        imgs = extract_out

        # Second: stitch pages together in OUTDIR
        OUTDIR = WORKDIR / "out"
        OUTDIR.mkdir()
        stitch(imgs, OUTDIR, skip_warning_page)

        # Third: rewrite cbz and overwrite if needed

        # Move old cbz if needed, otherwise will be overwritten
        if not del_old_cbz:
            shutil.move(cbz, ORIGINALCBZPATH)

        create_cbz(OUTDIR, cbz)

        logger.info(f"[{cbz.name}] Done!")


def extract(cbz: Path, out: Path) -> List[Path]:
    """
    Extract the given cbz to out and verify the pages of the cbz are the same size.
    Will insert blank pages if needed to guarantee the cbz returns an even
    number of pages so each page has a spread partner.
    If successful, returns list of paths to all images.

    :raises: FileNotFoundError if cbz doesn't exist or doesn't have a cbz suffix
    :raises: WrongImageSize if an image has a larger width or height than the last image
    """
    assert out.is_dir()

    if not cbz.exists():
        raise FileNotFoundError(
            f"[{cbz.name}] ERROR: {cbz} is not a valid path! Skipping file"
        )

    if cbz.suffix != ".cbz":
        raise FileNotFoundError(
            f"[{cbz.name}] ERROR: {cbz} is not a cbz! Skipping file"
        )

    shutil.unpack_archive(cbz, out, "zip")

    # In reverse, because we want right to left.
    imgs = sorted(out.iterdir(), reverse=True)

    # Ensure all images have same dimensions
    with Image.open(imgs[0]) as first_img:
        width = first_img.width
        height = first_img.height
        mode = first_img.mode

    blank_page_path = out / f"blank{imgs[0].suffix}"

    def create_blank_page():
        with Image.new(mode, (width, height), "white") as blank:
            blank.save(blank_page_path)

    def incorrect_dimensions(page: Image.Image) -> bool:
        # First condition: page has larger dimensions, is most likely a spread
        # Second condition: our initial width and height was a spread! check if
        # a doubled page is approximitely equal to width
        return (page.width > width or page.height > height) or (
            page.width * 2 >= (width - 100) and page.width * 2 <= (width + 100)
        )

    with Image.open(imgs[-1]) as first_page:
        # Special case: check if first page is wrong dimensions *and* all white
        # If so, we'll replace it with a white page of correct dimensions
        first_page_bad = False
        if incorrect_dimensions(first_page):
            colors = first_page.convert("RGBA").getcolors(1)
            first_page_bad = colors is not None and colors[0][1] == ImageColor.getcolor(
                "white", "RGBA"
            )

    if first_page_bad:
        create_blank_page()
        imgs[-1] = blank_page_path

    for img in imgs:
        with Image.open(img) as curr_page:
            if incorrect_dimensions(curr_page):
                raise WrongImageSize(
                    f"[{cbz.name}] {img} {curr_page.width}x{curr_page.height} doesn't match {width}x{height}!"
                )

    if len(imgs) % 2 != 0:
        # We have an odd amount of images. Add a blank page to the front of
        # the imgs array to add a blank page at the end of the chapter so every
        # page has a spread partner.
        if not blank_page_path.exists():
            create_blank_page()
        imgs.insert(0, blank_page_path)

    assert len(imgs) % 2 == 0

    return imgs


def stitch(imgs: List[Path], out: Path, skip_warning_page: bool):
    """Stitches the images together in pairs and writes those stitches
    to out. Will write a page warning readers to go to the back of the PDF
    unless skip_warning_page is false."""
    assert len(imgs) % 2 == 0

    # get width, height, mode
    with Image.open(imgs[0]) as first_img:
        width = first_img.width
        height = first_img.height
        mode = first_img.mode

    pagenum = 1

    # Add page to remind me to go to the last page, and avoid spoilers by
    # not immediately showing the last page of the chapter.
    # Help from https://stackoverflow.com/questions/16373425/add-text-on-image-using-pil
    # and https://stackoverflow.com/questions/1970807/center-middle-align-text-with-pil
    # If we only have 2 pages, don't insert - we only have one page to show!
    if not skip_warning_page and len(imgs) > 2:
        write_warning_page(
            out / f"{pagenum:03d}{imgs[0].suffix}", mode, width * 2, height
        )
        pagenum += 1

    # The list is already in reverse. Pop off 2 images, stick first one
    # on left, second on right, give it the right name, done
    # Made with help from https://stackoverflow.com/questions/10657383/stitching-photos-together
    while len(imgs) >= 2:
        with (
            Image.open(imgs.pop(0)) as img1,
            Image.open(imgs.pop(0)) as img2,
            Image.new(img1.mode, (width * 2, height), "white") as outimg,
        ):
            # Fill page with white, to account for smaller dimension pages
            # Assumes the background color *should* be white
            outimg.paste(im=img1, box=(0, 0))
            outimg.paste(im=img2, box=(width, 0))
            outimg.save(out / f"{pagenum:03d}.png")
            pagenum += 1

    assert len(imgs) == 0


def write_warning_page(out: Path, mode: str, width: int, height: int):
    """Writes a warning page to out."""
    from PIL import ImageDraw, ImageFont

    with Image.new(mode, (width, height), "white") as img:
        draw = ImageDraw.Draw(img)
        large_font = ImageFont.truetype(font_ttf, size=font_size)
        (_, _, text_width, text_height) = draw.textbbox(
            (0, 0), go_to_back_text, font=large_font
        )
        draw.text(
            ((width - text_width) / 2, (height - text_height) / 2),
            go_to_back_text,
            font=large_font,
            fill="black",
        )
        img.save(out)


def create_cbz(img_dir: Path, out: Path):
    """Given a directory of images, write a new cbz to out."""
    assert img_dir.is_dir()

    shutil.make_archive(str(out), "zip", img_dir)

    # make_archive adds a .zip to the end of the name, remove the .zip
    # Overwrites existing .cbz if del_old_cbz is true
    shutil.move(out.with_name(f"{out.name}.zip"), out)


def process_convert(cbz: Path, args: Namespace) -> bool:
    """Used by main() to run convert and catch exceptions."""
    try:
        convert(
            cbz,
            del_old_cbz=args.del_old_cbz,
            skip_warning_page=args.skip_warning_page,
            quiet=args.quiet,
        )
        return True
    except FileExistsError as e:
        logger.error(e)
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Correctly show manga spreads by stitching / merging / combining the pages of a cbz."
    )
    parser.add_argument(
        "cbzs", metavar="CBZ", type=str, nargs="+", help="cbzs to stitch"
    )
    parser.add_argument(
        "-d",
        "--del-old-cbzs",
        dest="del_old_cbz",
        action="store_true",
        default=False,
        help="Delete the original, unstitched cbzs instead of saving them",
    )
    parser.add_argument(
        "-w",
        "--skip-warning-page",
        dest="skip_warning_page",
        action="store_true",
        default=False,
        help="Do not put a warning page at the beginning of the cbz telling you the manga starts on the last page",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        dest="quiet",
        action="store_true",
        default=False,
        help="Do not print status updates. Errors will still be printed.",
    )
    parser.add_argument(
        "-v",
        "--volume",
        dest="volume",
        action="store_true",
        default=False,
        help="Put all cbzs into one cbz as a volume. Ordered in the same way as the arguments. Will be placed in the same folder as the first cbz.",
    )

    args = parser.parse_args()

    # Logging setup
    if args.quiet:
        level = logging.WARNING
    else:
        level = logging.INFO
    logger.setLevel(level)

    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger.addHandler(handler)

    # Don't make a volume if we only provided one chapter
    if args.volume and len(args.cbzs) > 1:
        try:
            convert_volume(
                list(map(Path, args.cbzs)),
                args.del_old_cbz,
                args.skip_warning_page,
                args.quiet,
            )
        except (FileExistsError, ChildProcessError) as e:
            logger.error(e)
            exit(1)
    else:
        with ProcessPoolExecutor() as p:
            results = p.map(partial(process_convert, args=args), map(Path, args.cbzs))

        if not all(results):
            exit(1)


if __name__ == "__main__":
    main()
