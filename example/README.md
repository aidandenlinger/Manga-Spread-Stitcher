# Example

`example.png` is the image at the start of the README.

`unmerged.cbz` is an example cbz that can be input to this script, and
`merged.cbz` is an example output. You can't view the `cbz`s on Github's web
interface, so I also converted them to PDFs so you can view them on Github.

`unmerged_odd.cbz` is an example with an odd number of pages. This is an
important test case because the script assumes every page has a neighbor to be
merged to, so it adds a blank page at the end. `merged_odd.cbz` is a successful
merge of this cbz file.