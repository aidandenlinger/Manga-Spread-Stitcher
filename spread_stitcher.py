#!/bin/python3
import shutil
import tempfile
from pathlib import Path
from PIL import Image, ImageColor
from sys import stderr


# Converts a cbz in-place to have merged pages.
def convert(cbz: Path, del_old_cbz: bool = False, skip_warning_page: bool = False, quiet: bool = False) -> bool:

    # FIRST PAGE MESSAGE CONFIG
    # This script currently assumes R to L mangas and generates it such that the
    # last page is the front of the PDF. By default, a page with this message will
    # be placed as the front page of the PDF.
    go_to_back_text = "This manga is read Right to Left! Go to the last page :)"
    # Font used to print the message.
    font_ttf = "arial.ttf"
    font_size = 40

    def eprint(*args): print(*args, file=stderr)

    if not cbz.exists():
        eprint(
            f"[{cbz.name}] ERROR: {cbz} is not a valid path! Skipping to next file")
        return False

    if cbz.suffix != ".cbz":
        eprint(f"[{cbz.name}] ERROR: Not a cbz! Skipping to next file")
        return False

    if not quiet:
        print(f"[{cbz.name}] Starting...")

    with tempfile.TemporaryDirectory() as tmpdir:
        WORKDIR = Path(tmpdir)
        ARCHIVEDIR = WORKDIR / "archive"
        OUTDIR = WORKDIR / "out"

        shutil.unpack_archive(cbz, ARCHIVEDIR, "zip")

        # In reverse, because we want right to left.
        imgs = sorted(ARCHIVEDIR.iterdir(), reverse=True)

        # This script assumes that pages should be paired like
        # 1-2 / 3-4 / 5-6 / 7
        # The cbz files I often use this for will have page 1 be a completely
        # blank white page if page 2 should be by itself in a spread, so this
        # works for my usecases.
        # Since we generate the pages backwards, we need to know if
        # the last page should be by itself at the beginning. Namely, if we
        # have an odd number of pages, we need to ensure page 7 is generated
        # without a spread partner.
        # We need to note this before we analyze page 1: it may be a blank page
        # that we eliminate, so we need to check page count before we eliminate
        # it
        last_page_by_itself = len(imgs) % 2 == 1

        # Ensure all images have same dimensions
        with Image.open(imgs[0]) as first_img:
            width = first_img.width
            height = first_img.height
            mode = first_img.mode

        # Iterate through all images except img[0], which we just got dimensions
        # for, and img[-1], which we will check for errors after
        for img in imgs[1:-1]:
            with Image.open(img) as curr_page:
                if curr_page.width > width or curr_page.height > height:
                    eprint(
                        f"[{cbz.name}] ERROR: {img} {curr_page.width}x{curr_page.height} doesn't match {width}x{height}! Skipping...")
                    return False

        # I've had experiences where the first page is just completely white
        # and/or has the wrong dimensions. We'll check both of these
        # issues here, and if so remove the first page to ensure an all white
        # page with wrong dimensions doesn't terminate conversion. A blank page
        # will be inserted if needed.
        with Image.open(imgs[-1]) as first_page:
            # Do we have one color, and is that one color white?
            # If so, disregard image, irregardless of dimensions
            colors = first_page.convert("RGBA").getcolors(1)
            if colors and colors[0][1] == ImageColor.getcolor("white", "RGBA"):
                imgs.pop()
            # Else, this is an actual page, handle as normal.
            elif first_page.width > width or first_page.height > height:
                eprint(
                    f"[{cbz.name}] ERROR: {imgs[-1]} {first_page.width}x{first_page.height} doesn't match {width}x{height}! Skipping...")
                return False

        # Second: stitch pages together
        OUTDIR.mkdir()
        count = 1

        # Add page to remind me to go to the last page, and avoid spoilers by
        # not immediately showing the last page of the chapter.
        # Help from https://stackoverflow.com/questions/16373425/add-text-on-image-using-pil
        # and https://stackoverflow.com/questions/1970807/center-middle-align-text-with-pil
        # If we only have 2 or 1 pages, don't insert.
        if not skip_warning_page and ((last_page_by_itself and len(imgs) > 1) or len(imgs) > 2):
            from PIL import ImageDraw, ImageFont
            img = Image.new(mode, (width*2, height))
            img.paste(ImageColor.getcolor("white", mode),
                      box=(0, 0, width*2, height))
            draw = ImageDraw.Draw(img)
            large_font = ImageFont.truetype(font_ttf, size=font_size)
            (_, _, text_width, text_height) = draw.textbbox(
                (0, 0), go_to_back_text, font=large_font)
            draw.text(((width * 2 - text_width)/2, (height - text_height)/2),
                      go_to_back_text, font=large_font, fill="black")
            img.save(OUTDIR / f"{count:03d}.png")
            count += 1

        # If the last page doesn't have a spread partner, paste it by itself
        # Eg 1-2 / 3-4 / 5; 5 doesn't have a partner
        if last_page_by_itself:
            with Image.open(imgs.pop(0)) as img:
                out = Image.new(img.mode, (width*2, height))
                # Fill image with white
                out.paste(ImageColor.getcolor("white", img.mode),
                          box=(0, 0, width * 2, height))
                out.paste(im=img, box=(width, 0))
                out.save(OUTDIR / f"{count:03d}.png")
                count += 1

        # The list is already in reverse. Pop off 2 images, stick first one
        # on left, second on right, give it the right name, done
        # Made with help from https://stackoverflow.com/questions/10657383/stitching-photos-together
        while len(imgs) >= 2:
            with (Image.open(imgs.pop(0)) as img1, Image.open(imgs.pop(0)) as img2):
                out = Image.new(img1.mode, (width * 2, height))
                out.paste(im=img1, box=(0, 0))
                out.paste(im=img2, box=(width, 0))

                out.save(OUTDIR / f"{count:03d}.png")
                count += 1

        if len(imgs) == 1:
            # Same code but we don't paste img2 because there is no img2
            with Image.open(imgs.pop(0)) as img:
                out = Image.new(img.mode, (width*2, height))
                # Fill image with white
                out.paste(ImageColor.getcolor("white", img.mode),
                          box=(0, 0, width*2, height))
                out.paste(im=img, box=(0, 0))
                out.save(OUTDIR / f"{count:03d}.png")
                count += 1

        # Third: rewrite cbz
        shutil.make_archive(cbz, "zip", OUTDIR)

        # Move old cbz if needed, otherwise will be overwritten
        if not del_old_cbz:
            shutil.move(cbz, cbz.with_stem(
                cbz.stem + "_original"))
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
