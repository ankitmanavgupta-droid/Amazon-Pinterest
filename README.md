# Amazon → Pinterest

Turns Amazon product links into a "shop the look" Pinterest pin: it fetches each
product's photo, cuts out the background, lays them out on a pin you arrange by
hand, publishes a landing page where every product in the image is separately
clickable, and posts the pin to Pinterest.

You don't have to bring the links either — describe a look and it finds
products that suit it, judging the photos rather than just the star ratings.

Every product link is region-aware — a UK visitor and a US visitor clicking the
same product each go to their own Amazon store, with the matching Associates tag.

## Setup

```bash
python3.13 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then fill it in
```

Keys you'll need in `.env`:

| Variable | What it's for | Where to get it |
|---|---|---|
| `AMAZON_AFFILIATE_TAG` | US affiliate links | associates.amazon.com |
| `AMAZON_AFFILIATE_TAG_UK` | UK affiliate links (optional) | affiliate-program.amazon.co.uk |
| `CANOPY_API_KEY` | Product titles + images | canopyapi.co (100 free requests/month) |
| `ANTHROPIC_API_KEY` | SEO titles, descriptions, hashtags | console.anthropic.com |
| `ZERNIO_API_KEY` | Posting to Pinterest and TikTok | zernio.com |

Regions are only built for tags you actually set, so a UK-only account just
produces UK links.

## Using it

```bash
python app.py       # then open http://localhost:5000
```

Everything happens on that one page:

1. **Find** (optional) — describe a look ("early autumn cottagecore tops") and
   get back scored products to tick and drop into the builder. See below.
2. **Build** — paste a title and up to 6 Amazon links. Products are fetched,
   backgrounds removed, and an SEO title/description/hashtags written.
3. **Edit** — drag the cutouts and text around the pin. Saving stores the
   layout, so you can reopen and adjust a pin later.
4. **Publish & post** — pushes the landing page to GitHub Pages and posts the
   pin to Pinterest, onto whichever board fits best. Or pick a date and time
   and hit **Schedule** to have it go out later.

Short links (`amzn.to`, `link.amazon`) and sponsored search links all work —
they get resolved to the real product first.

### From the terminal instead

```bash
python build_pin.py links.txt   # bulk-build from a file
python publish_pin.py           # publish + post everything that's ready
python post_pin.py <slug>       # re-post a single pin
python schedule_pin.py <slug> "2026-08-20T09:30" [Europe/London]   # post it later
python find_products.py "coquette bedroom decor" --append          # find products
```

`links.txt` format — two `#` lines for the title, then that pin's product URLs:

```
#Summer Tops
#From Amazon
https://www.amazon.co.uk/dp/...
https://www.amazon.com/dp/...
```

## A second template: generated images

The dashboard's "New pin" panel has a **Template** dropdown. Alongside the
product collage above, **Generated image** covers a different workflow: you
give it one Amazon link, it hands you back a folder structure, and a manual
step in ChatGPT does the actual image-making.

```
incoming-clothes/     reference photos, waiting for a generated image
generated-images/     ChatGPT's finished PNGs, saved by hand — never emptied
processed-inputs/     reference photos already matched to a pin (archive)
failed-inputs/        reference photos you abandoned (archive)
.imagegen-state.json  which generated-images/ files are already used
```

1. Paste a product link (dashboard, or `python add_clothes_link.py <url>`).
   The product photo is fetched and saved to `incoming-clothes/<slug>.jpg` —
   that's the reference image.
2. Generate a pin from it yourself, in ChatGPT — this app has no part in that
   step. Save the result as `generated-images/<slug>.png`.
3. `python app.py` checks for new files there every minute automatically (or
   run `python sync_generated.py` once by hand, e.g. from cron). Once matched,
   the pin is posted **as-is** — no editor, no text overlay — linking straight
   back to the product it came from.

`generated-images/` is a permanent archive, never cleared out — the state file
is what stops a re-scan from redoing a match it's already made. A pending
reference photo you decide not to use can be moved to `failed-inputs/` with
the dashboard's "Mark as failed" button, or `mark_failed()` from the terminal.

## Outfit Studio: a wardrobe you generate looks from

`/wardrobe` is a persistent closet, separate from any one pin. Drop garment
photos into it, drag them between category tabs to sort, and it keeps the
background-removed cutouts under `wardrobe-items/`.

```
wardrobe-items/
  wardrobe.json     sections, items (incl. archived), saved recipes
  raw/itemN.<ext>   the original upload
  cutout/itemN.png  background removed
```

**Generating.** A *recipe* says what an outfit is made of. Tick **Combine all**
on a section and every one of its items is used; leave it unticked and set a
count instead, and that many are picked at random per outfit. Combining tops
and jeans gives you every top/jeans pairing, each with its own random watch,
belt and fragrance.

**Archiving.** ☐ on an item takes it out of future random pulls without
deleting it; ↺ (under "Show archived items") puts it back. **×** deletes for
good. After a run, **Archive items used** clears the whole batch at once.

**TikTok slideshows.** Generated outfits are grouped into batches of three for
posting as a TikTok photo carousel. Within a batch the same top never appears
twice, and the same bottom won't either unless the recipe leaves no choice —
a batch that had to repeat one says so. Drag a slide between batches to
regroup. Each outfit still posts to Pinterest individually as its own pin;
the slideshow is an additional destination, not a replacement.

Posting to TikTok goes through Zernio like Pinterest does, so connect a TikTok
account in their dashboard first.

Pins are 2:3, which is the ratio Pinterest wants — taller ones risk being
cropped in their feed. TikTok's carousel is full-screen 9:16 and pads anything
squarer with blurred fill, so each slide is re-framed to 1080×1920 on the way
out (`posts/<slug>/tiktok.png`): same composition at the same size, centred,
with the background continued into the extra height. Flat backgrounds extend
seamlessly, so there are no bars.

TikTok rations direct posting. When that capacity runs out, the slideshow is
delivered to the account's **Creator Inbox** instead — Zernio reports that as
"published" because it handed it over, but it isn't live until you open the
TikTok app, go to the notifications tab and finish the post. Batches in that
state say so, and can be reset if the draft never turns up.

## Posting on an interval instead of one at a time

The **Auto-post queue** panel turns on "post every N hours": instead of
picking a time for each pin, whatever's published-but-not-yet-posted (either
template) gets handed to Zernio in a fixed cadence, oldest first. Zernio holds
and fires each slot from its own servers — a slot already assigned goes out
even with this app closed. Running the app (or `python sync_generated.py`) is
only needed to pick up newly-ready pins and give them their turn.

Scheduling a pin for one specific time, from the dashboard or
`schedule_pin.py`, still works exactly as before — the two aren't exclusive,
but a pin only ever gets one or the other.

## Finding products

```bash
python find_products.py "early autumn cottagecore tops"
```

Claude turns the look into Amazon search terms, each search is run, and the
results are filtered on the boring stuff first — sponsored slots dropped, then
anything under 4★ or with fewer than 20 reviews. Only what survives gets its
photo downloaded, because that's the expensive part.

Then Claude looks at the photos. This is the part that matters: what ends up on
a pin isn't the product, it's the product's photo with its background removed.
So a listing whose image has a price badge burned into the corner, or three
angles collaged together, is no use however good the product is — and that only
shows up by looking. Each product comes back with a score and the reason for it
("clean cutout, soft neutral knit" / "watermark across the sleeve").

One Amazon search returns a lot of the same seller, and every colour of a top
is its own listing, so near-identical titles are collapsed and no more than two
products per brand come back — six variations of one blouse isn't a shop-the-look
pin.

Searches are cached under `.cache/searches/` for a week. That matters: Canopy's
free tier is 100 requests a month, one per search term, and building pins spends
from the same pot. Three terms per run is the default; `--terms` changes it.

Add `--append` to write the picks straight into `links.txt`.

## How a pin is stored

```
posts/<slug>/
  pin.json          titles, products, affiliate links, layout, SEO, status
  raw/productN.jpg  original product photo
  cutout/productN.png  background removed, cropped tight
  pin.png           the rendered pin image
docs/shop/<slug>.html   the published landing page (+ its .png)
```

`pin.json` is the single source of truth; status is derived from it and from
what's on disk, so nothing can drift out of sync.

## Notes

- **Pinterest posting** currently goes through Zernio, because a Pinterest
  developer app needs approved access before it can post. `pinterest/client.py`
  and `pinterest/auth.py` talk to Pinterest's own API directly and are ready for
  when that access comes through — Pinterest's API is free, Zernio is not.
- **Pins can't be deleted through the API.** If a wrong pin goes out, remove it
  in the Pinterest app.
- **Board sections** aren't supported by Zernio's API, so pins land on a board
  rather than a section within it.
- **Scheduling** hands the pin to Zernio with a time attached, and they publish
  it — so it still goes out with this machine off. The landing page can't wait,
  though: it's pushed live at the moment you schedule, since the pin links to
  it. Zernio's API can't cancel a scheduled post, so a wrong time has to be
  fixed in their dashboard.
