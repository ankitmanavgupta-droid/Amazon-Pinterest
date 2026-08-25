"""Local web app: build, edit, publish and post pins from one page.

Run it with:  python app.py     (then open http://localhost:5000)
"""

import base64
import os
import threading
import time
import uuid

import requests
from flask import Flask, abort, jsonify, request, send_file, send_from_directory

import discovery
import generated_pins
import pins
import slideshows
import wardrobe
from config import DISCOVERY_DOMAIN, GITHUB_PAGES_BASE_URL, MAX_PRODUCTS, TEMPLATES_DIR
from publishing import PublishError, post_pin, post_slideshow, publish_pin, schedule_pin
from zernio import ZernioAPIError, get_connected_pinterest_accounts, list_boards

app = Flask(__name__, template_folder=str(TEMPLATES_DIR))


@app.errorhandler(Exception)
def _json_errors(error):
    """Every /api caller parses the response as JSON, so an unhandled
    exception rendering Flask's HTML error page surfaces in the browser as
    "Unexpected token '<'" rather than saying what actually broke."""
    from werkzeug.exceptions import HTTPException

    status = error.code if isinstance(error, HTTPException) else 500
    if not request.path.startswith("/api/"):
        return error
    if isinstance(error, HTTPException):
        return jsonify({"error": error.description}), status
    app.logger.exception("Unhandled error on %s", request.path)
    return jsonify({"error": f"{type(error).__name__}: {error}"}), 500


# ---------- Background jobs ----------
# Builds take a while (network round-trips per product, then background
# removal), so they run off-thread and the page polls for progress.
_jobs = {}
_jobs_lock = threading.Lock()


def _new_job() -> str:
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "messages": [], "result": None, "error": None}
    return job_id


def _job_update(job_id: str, **fields):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            job.update(fields)


def _job_message(job_id: str, message: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            job["messages"].append(message)


def _run_job(job_id: str, work):
    try:
        result = work(lambda message: _job_message(job_id, message))
        _job_update(job_id, status="done", result=result)
    except Exception as e:
        _job_update(job_id, status="error", error=str(e))


# ---------- Pinterest boards (cached briefly — the SEO step needs the names) ----------
_boards_cache = {"fetched_at": 0, "account_id": None, "boards": []}
BOARDS_CACHE_SECONDS = 300


def get_boards(force: bool = False) -> list:
    if not force and time.time() - _boards_cache["fetched_at"] < BOARDS_CACHE_SECONDS:
        return _boards_cache["boards"]

    accounts = get_connected_pinterest_accounts()
    if not accounts:
        raise ZernioAPIError("No Pinterest account connected in Zernio.")

    account_id = accounts[0]["_id"]
    boards = list_boards(account_id)
    _boards_cache.update({"fetched_at": time.time(), "account_id": account_id, "boards": boards})
    return boards


# ---------- Pages ----------

@app.get("/")
def dashboard():
    return send_from_directory(TEMPLATES_DIR, "dashboard.html")


@app.get("/pin/<slug>")
def editor(slug):
    try:
        pins.load_pin(slug)
    except pins.PinNotFoundError:
        abort(404)
    return send_from_directory(TEMPLATES_DIR, "pin_builder.html")


@app.get("/outfit/<slug>")
def outfit_editor(slug):
    try:
        pin = pins.load_pin(slug)
    except pins.PinNotFoundError:
        abort(404)
    if pin.get("template") != "outfit":
        abort(404)
    return send_from_directory(TEMPLATES_DIR, "outfit_builder.html")


@app.get("/wardrobe")
def wardrobe_page():
    return send_from_directory(TEMPLATES_DIR, "wardrobe.html")


# ---------- Pin API ----------

@app.get("/api/pins")
def api_list_pins():
    return jsonify([pins.pin_summary(pin) for pin in pins.list_pins()])


@app.post("/api/pins")
def api_create_pin():
    data = request.get_json(force=True)
    title1 = (data.get("title1") or "").strip()
    title2 = (data.get("title2") or "").strip()
    urls = [u.strip() for u in (data.get("urls") or []) if u.strip()]

    if not title1:
        return jsonify({"error": "A title is required."}), 400
    if not urls:
        return jsonify({"error": "At least one product URL is required."}), 400
    if len(urls) > MAX_PRODUCTS:
        return jsonify({"error": f"At most {MAX_PRODUCTS} products per pin."}), 400

    try:
        board_names = [b["name"] for b in get_boards()]
    except Exception:
        board_names = None  # SEO still works, it just won't pre-pick a board

    job_id = _new_job()
    thread = threading.Thread(
        target=_run_job,
        args=(job_id, lambda report: pins.build_pin(title1, title2, urls, board_names=board_names, progress=report)),
        daemon=True,
    )
    thread.start()
    return jsonify({"jobId": job_id}), 202


@app.post("/api/generated-pins")
def api_create_generated_pin():
    """Starts a generated-image pin: resolves the link to its product photo
    and drops that in incoming-clothes/ as the reference image for the manual
    ChatGPT step. Fast enough (one product fetch) to not need a job/poll."""
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    title = (data.get("title") or "").strip()

    if not url:
        return jsonify({"error": "A product link is required."}), 400

    try:
        pin = pins.create_generated_pin(url, title=title)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(pins.pin_summary(pin)), 201


@app.post("/api/outfit-pins")
def api_create_outfit_pin():
    data = request.get_json(force=True)
    title1 = (data.get("title1") or "Outfit").strip()
    title2 = (data.get("title2") or "Edit").strip()
    pin = pins.create_outfit_pin(title1, title2)
    return jsonify(pins.pin_summary(pin)), 201


@app.post("/api/outfit-pins/<slug>/assets")
def api_upload_outfit_assets(slug):
    category = (request.form.get("category") or "").strip().lower()
    files = [upload for upload in request.files.getlist("files") if upload and upload.filename]
    image_urls = [url.strip() for url in request.form.getlist("image_urls") if url.strip()]
    try:
        valid_categories = {section["id"] for section in pins.outfit_sections(pins.load_pin(slug))}
    except pins.PinNotFoundError:
        abort(404)
    if category not in valid_categories:
        return jsonify({"error": "Choose a valid wardrobe section."}), 400
    if not files and not image_urls:
        return jsonify({"error": "Choose an image or drag one from another website."}), 400

    added = []
    errors = []
    for upload in files:
        try:
            product = pins.add_outfit_asset(slug, category, upload.read(), upload.filename)
            added.append(product)
        except Exception as e:
            errors.append(f"{upload.filename}: {e}")
    for image_url in image_urls:
        try:
            product = pins.add_outfit_remote_asset(slug, category, image_url)
            added.append(product)
        except Exception as e:
            errors.append(f"Web image: {e}")
    if not added:
        return jsonify({"error": "; ".join(errors) or "No images could be processed."}), 400
    return jsonify({"products": added, "errors": errors}), 201


@app.post("/api/outfit-pins/<slug>/sections")
def api_add_outfit_section(slug):
    data = request.get_json(force=True)
    try:
        section = pins.add_outfit_section(slug, data.get("label") or "")
    except pins.PinNotFoundError:
        abort(404)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(section), 201


@app.patch("/api/outfit-pins/<slug>/assets/<asset_id>")
def api_move_outfit_asset(slug, asset_id):
    data = request.get_json(force=True)
    try:
        product = pins.move_outfit_asset(slug, asset_id, (data.get("category") or "").strip())
    except pins.PinNotFoundError:
        abort(404)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(product)


@app.delete("/api/outfit-pins/<slug>/assets/<asset_id>")
def api_delete_outfit_asset(slug, asset_id):
    try:
        pins.delete_outfit_asset(slug, asset_id)
    except pins.PinNotFoundError:
        abort(404)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


# ---------- Wardrobe API (the shared closet behind the outfit generator) ----------

def _wardrobe_payload() -> dict:
    data = wardrobe.load_wardrobe()
    items = [dict(item) for item in data.get("items", [])]
    for item in items:
        item["cutoutUrl"] = f"/api/wardrobe/items/{item['id']}/cutout?v={item.get('v', 1)}"
    return {"sections": wardrobe.wardrobe_sections(data), "items": items, "recipes": data.get("recipes", [])}


@app.get("/api/wardrobe")
def api_get_wardrobe():
    return jsonify(_wardrobe_payload())


@app.post("/api/wardrobe/sections")
def api_add_wardrobe_section():
    data = request.get_json(force=True)
    try:
        section = wardrobe.add_wardrobe_section(data.get("label") or "")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(section), 201


@app.delete("/api/wardrobe/sections/<section_id>")
def api_delete_wardrobe_section(section_id):
    """?moveTo=<section> refiles this section's items; without it they're
    deleted along with the section."""
    try:
        wardrobe.delete_wardrobe_section(section_id, (request.args.get("moveTo") or "").strip() or None)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(_wardrobe_payload())


@app.post("/api/wardrobe/items")
def api_upload_wardrobe_items():
    category = (request.form.get("category") or "").strip().lower()
    files = [upload for upload in request.files.getlist("files") if upload and upload.filename]
    image_urls = [url.strip() for url in request.form.getlist("image_urls") if url.strip()]
    valid_categories = {section["id"] for section in wardrobe.wardrobe_sections(wardrobe.load_wardrobe())}
    if category not in valid_categories:
        return jsonify({"error": "Choose a valid wardrobe section."}), 400
    if not files and not image_urls:
        return jsonify({"error": "Choose an image or drag one from another website."}), 400

    added = []
    errors = []
    for upload in files:
        try:
            added.append(wardrobe.add_wardrobe_item(category, upload.read(), upload.filename))
        except Exception as e:
            errors.append(f"{upload.filename}: {e}")
    for image_url in image_urls:
        try:
            added.append(wardrobe.add_wardrobe_remote_item(category, image_url))
        except Exception as e:
            errors.append(f"Web image: {e}")
    if not added:
        return jsonify({"error": "; ".join(errors) or "No images could be processed."}), 400
    for item in added:
        item["cutoutUrl"] = f"/api/wardrobe/items/{item['id']}/cutout?v={item.get('v', 1)}"
    return jsonify({"items": added, "errors": errors}), 201


@app.patch("/api/wardrobe/items/<item_id>")
def api_move_wardrobe_item(item_id):
    data = request.get_json(force=True)
    try:
        item = wardrobe.move_wardrobe_item(item_id, (data.get("category") or "").strip())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(item)


@app.post("/api/wardrobe/items/archive")
def api_archive_wardrobe_items():
    data = request.get_json(force=True)
    return jsonify({"items": wardrobe.archive_wardrobe_items(data.get("ids") or [])})


@app.post("/api/wardrobe/items/restore")
def api_restore_wardrobe_items():
    data = request.get_json(force=True)
    return jsonify({"items": wardrobe.restore_wardrobe_items(data.get("ids") or [])})


@app.delete("/api/wardrobe/items/<item_id>")
def api_delete_wardrobe_item(item_id):
    try:
        wardrobe.delete_wardrobe_item(item_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.get("/api/wardrobe/items/<item_id>/cutout")
def api_wardrobe_item_cutout(item_id):
    data = wardrobe.load_wardrobe()
    item = next((entry for entry in data.get("items", []) if entry.get("id") == item_id), None)
    if not item:
        abort(404)
    path = wardrobe.WARDROBE_DIR / "cutout" / item["cutout"]
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="image/png")


@app.post("/api/wardrobe/recipes")
def api_add_wardrobe_recipe():
    data = request.get_json(force=True)
    try:
        recipe = wardrobe.add_recipe(data.get("name") or "", data.get("combine") or [], data.get("random") or {})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(recipe), 201


@app.delete("/api/wardrobe/recipes/<recipe_id>")
def api_delete_wardrobe_recipe(recipe_id):
    wardrobe.delete_recipe(recipe_id)
    return jsonify({"ok": True})


@app.post("/api/wardrobe/generate")
def api_generate_wardrobe():
    data = request.get_json(force=True)
    # Titles only name the slug/folder here — generated pins render no text.
    title1 = (data.get("title1") or "Outfit").strip()
    title2 = (data.get("title2") or "").strip()
    combine = data.get("combine") or []
    random_spec = data.get("random") or {}
    try:
        created = wardrobe.generate_outfit_batch(title1, title2, combine, random_spec)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    slideshows.create_batches_for([pin["slug"] for pin in created])
    return jsonify({
        "pins": [_pin_payload(pin["slug"]) for pin in created],
        "slideshows": slideshows.list_slideshows(),
    }), 201


# ---------- TikTok slideshows (generated outfits, batched) ----------

@app.get("/api/slideshows")
def api_list_slideshows():
    return jsonify(slideshows.list_slideshows())


@app.put("/api/slideshows")
def api_set_slideshow_arrangement():
    """Replaces the whole grouping — what the preview grid sends after a slide
    is dragged from one batch to another."""
    data = request.get_json(force=True)
    try:
        slideshows.set_arrangement(data.get("slideshows") or [])
    except slideshows.SlideshowError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(slideshows.list_slideshows())


@app.patch("/api/slideshows/<slideshow_id>")
def api_update_slideshow(slideshow_id):
    data = request.get_json(force=True)
    try:
        slideshows.update_slideshow(slideshow_id, caption=(data.get("caption") or "").strip())
    except slideshows.SlideshowError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(slideshows.slideshow_summary(slideshows.get_slideshow(slideshow_id)))


@app.delete("/api/slideshows/<slideshow_id>")
def api_delete_slideshow(slideshow_id):
    slideshows.delete_slideshow(slideshow_id)
    return jsonify({"ok": True})


@app.post("/api/slideshows/<slideshow_id>/undraft")
def api_undraft_slideshow(slideshow_id):
    """Clears the 'sent to TikTok drafts' mark so the batch can be posted
    again — for a Creator Inbox delivery that never arrived, or was discarded
    in the app."""
    try:
        slideshows.update_slideshow(slideshow_id, drafted_at=None)
    except slideshows.SlideshowError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(slideshows.slideshow_summary(slideshows.get_slideshow(slideshow_id)))


@app.post("/api/slideshows/<slideshow_id>/post")
def api_post_slideshow(slideshow_id):
    data = request.get_json(silent=True) or {}
    try:
        tiktok_url = post_slideshow(
            slideshow_id, caption=data.get("caption"), draft=bool(data.get("draft")),
        )
    except (PublishError, slideshows.SlideshowError, ZernioAPIError) as e:
        return jsonify({"error": str(e)}), 400
    except requests.RequestException as e:
        return jsonify({"error": f"Couldn't reach Zernio: {e}"}), 400

    show = slideshows.get_slideshow(slideshow_id)
    return jsonify({"tiktokUrl": tiktok_url, "draftedAt": show.get("drafted_at")})


@app.post("/api/pins/<slug>/reroll")
def api_reroll_pin(slug):
    data = request.get_json(force=True)
    categories = data.get("categories")
    try:
        pins.reroll_generated_categories(slug, categories)
    except pins.PinNotFoundError:
        abort(404)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(_pin_payload(slug))


@app.post("/api/pins/<slug>/mark-failed")
def api_mark_failed(slug):
    """Moves a pending reference image to failed-inputs/ — for when the
    ChatGPT step was abandoned rather than just not done yet."""
    try:
        generated_pins.mark_failed(slug)
    except generated_pins.ImageGenError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.post("/api/sync-generated")
def api_sync_generated():
    """Checks generated-images/ right now, instead of waiting for the next
    background poll (see _start_background_poller)."""
    try:
        board_names = [b["name"] for b in get_boards()]
    except Exception:
        board_names = None  # SEO still works, it just won't pre-pick a board

    matched = generated_pins.sync_incoming_images(board_names=board_names)
    return jsonify({"matched": matched})


@app.get("/api/drip")
def api_get_drip():
    return jsonify(generated_pins.drip_status())


@app.post("/api/drip")
def api_set_drip():
    data = request.get_json(force=True)

    if data.get("enabled") is False:
        return jsonify(generated_pins.disable_drip())

    try:
        interval_hours = float(data.get("intervalHours"))
    except (TypeError, ValueError):
        return jsonify({"error": "intervalHours must be a number."}), 400

    try:
        config = generated_pins.enable_drip(
            interval_hours,
            start_at=(data.get("startAt") or "").strip() or None,
            tz_name=(data.get("timezone") or "").strip() or None,
        )
    except (generated_pins.ImageGenError, PublishError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(config)


@app.post("/api/pins/batch")
def api_create_pins_batch():
    """Builds several product-collage pins in one run, all under one title —
    groups is a list of URL-lists, one pin per group."""
    data = request.get_json(force=True)
    title1 = (data.get("title1") or "").strip()
    title2 = (data.get("title2") or "").strip()
    groups = [[u.strip() for u in g if u.strip()] for g in (data.get("groups") or [])]
    groups = [g for g in groups if g]

    if not title1:
        return jsonify({"error": "A title is required."}), 400
    if not groups:
        return jsonify({"error": "At least one group of product URLs is required."}), 400
    if any(len(g) > MAX_PRODUCTS for g in groups):
        return jsonify({"error": f"At most {MAX_PRODUCTS} products per pin."}), 400

    try:
        board_names = [b["name"] for b in get_boards()]
    except Exception:
        board_names = None

    job_id = _new_job()
    thread = threading.Thread(
        target=_run_job,
        args=(job_id, lambda report: pins.build_pins_batch(
            title1, title2, groups, board_names=board_names, progress=report,
        )),
        daemon=True,
    )
    thread.start()
    return jsonify({"jobId": job_id}), 202


@app.post("/api/generated-pins/batch")
def api_create_generated_pins_batch():
    """Starts several generated-image pins in one run, all under one title —
    one per link."""
    data = request.get_json(force=True)
    title = (data.get("title") or "").strip()
    urls = [u.strip() for u in (data.get("urls") or []) if u.strip()]

    if not urls:
        return jsonify({"error": "At least one product link is required."}), 400

    job_id = _new_job()
    thread = threading.Thread(
        target=_run_job,
        args=(job_id, lambda report: pins.create_generated_pins_batch(title, urls, progress=report)),
        daemon=True,
    )
    thread.start()
    return jsonify({"jobId": job_id}), 202


@app.post("/api/discover")
def api_discover():
    """Finds products for a vibe. Slow (a minute per Amazon search), so it runs
    as a job and the page polls it, same as building a pin."""
    data = request.get_json(force=True)
    vibe = (data.get("vibe") or "").strip()
    if not vibe:
        return jsonify({"error": "Describe the look you're after."}), 400

    term_count = max(1, min(int(data.get("terms") or 3), 5))
    domain = (data.get("domain") or DISCOVERY_DOMAIN).strip().upper()

    try:
        board_names = [b["name"] for b in get_boards()]
    except Exception:
        board_names = None  # only used to steer the search terms

    job_id = _new_job()
    thread = threading.Thread(
        target=_run_job,
        args=(job_id, lambda report: discovery.discover(
            vibe, domain=domain, term_count=term_count, board_names=board_names, progress=report,
        )),
        daemon=True,
    )
    thread.start()
    return jsonify({"jobId": job_id}), 202


@app.get("/api/jobs/<job_id>")
def api_job(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            abort(404)
        return jsonify(dict(job))


def _pin_payload(slug: str) -> dict:
    """The shape the editor/preview grid need: status, liveUrl and a versioned
    cutoutUrl per product. Shared by the single-pin editor and the
    wardrobe-generator's create/reroll responses."""
    pin = dict(pins.load_pin(slug))
    pin["status"] = pins.pin_status(pin)
    pin["liveUrl"] = pins.pin_summary(pin)["liveUrl"]
    for index, product in enumerate(pin.get("products", []), start=1):
        # The numeric route can point at a different product after an item is
        # deleted or rerolled. Version with "v" (bumped on reroll) falling back
        # to the stable product id, so the browser cannot show a cached image
        # for the old occupant of this list position.
        version = product.get("v") or product.get("id") or product.get("cutout") or index
        product["cutoutUrl"] = f"/api/pins/{slug}/cutout/{index}?v={version}"
    return pin


@app.get("/api/pins/<slug>")
def api_get_pin(slug):
    try:
        return jsonify(_pin_payload(slug))
    except pins.PinNotFoundError:
        abort(404)


@app.get("/api/pins/<slug>/cutout/<int:index>")
def api_cutout(slug, index):
    if index < 1:
        abort(404)
    try:
        pin = pins.load_pin(slug)
        product = pin.get("products", [])[index - 1]
    except (pins.PinNotFoundError, IndexError):
        abort(404)
    cutout_name = product.get("cutout") or f"product{index}.png"
    if "/" in cutout_name or "\\" in cutout_name:
        abort(404)
    path = pins.pin_dir(slug) / "cutout" / cutout_name
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="image/png")


@app.get("/api/pins/<slug>/image")
def api_pin_image(slug):
    path = pins.pin_dir(slug) / "pin.png"
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="image/png")


@app.post("/api/pins/<slug>/save")
def api_save_pin(slug):
    """Called by the editor: stores the rendered image, the hotspot geometry
    (as a landing page) and the layout so the pin can be re-opened later."""
    try:
        pin = pins.load_pin(slug)
    except pins.PinNotFoundError:
        abort(404)

    data = request.get_json(force=True)
    image_data_url = data.get("image") or ""
    if "," not in image_data_url:
        return jsonify({"error": "Missing rendered image."}), 400

    image_bytes = base64.b64decode(image_data_url.split(",", 1)[1])
    (pins.pin_dir(slug) / "pin.png").write_bytes(image_bytes)

    # Only the collage template publishes a page of its own; an outfit links
    # straight at a product instead (see pins.destination_link).
    if pins.has_landing_page(pin):
        pins.write_landing_page(slug, data.get("hotspots") or [])

    if data.get("layout"):
        pin["layout"] = data["layout"]
    if data.get("seo"):
        pin["seo"] = {**(pin.get("seo") or {}), **data["seo"]}
    pins.save_pin(pin)

    return jsonify({"ok": True, "status": pins.pin_status(pin)})


@app.post("/api/pins/<slug>/publish")
def api_publish(slug):
    try:
        live_url = publish_pin(slug)
    except (PublishError, pins.PinNotFoundError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"liveUrl": live_url})


@app.post("/api/pins/<slug>/post")
def api_post(slug):
    try:
        pin_url = post_pin(slug)
    except (PublishError, pins.PinNotFoundError, ZernioAPIError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"pinUrl": pin_url})


@app.post("/api/pins/<slug>/schedule")
def api_schedule(slug):
    """Hands the pin to Zernio to post later. The browser sends its own timezone
    alongside the wall-clock time, so 9am means 9am where the user is."""
    data = request.get_json(force=True)
    when = (data.get("at") or "").strip()
    if not when:
        return jsonify({"error": "A date and time is required."}), 400

    try:
        scheduled_for = schedule_pin(slug, when, tz_name=(data.get("timezone") or "").strip() or None)
    except (PublishError, pins.PinNotFoundError, ZernioAPIError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"scheduledFor": scheduled_for})


@app.delete("/api/pins/<slug>")
def api_delete_pin(slug):
    pins.delete_pin(slug)
    return jsonify({"ok": True})


@app.get("/api/config")
def api_config():
    return jsonify({
        "shopUrl": GITHUB_PAGES_BASE_URL,
        "maxProducts": MAX_PRODUCTS,
        "discoveryDomain": DISCOVERY_DOMAIN,
        "sourceMtime": _newest_source_mtime(),
        "startedAt": _STARTED_AT,
    })


_STARTED_AT = time.time()
_SOURCE_FILES = ("app.py", "publishing.py", "pins.py", "slideshows.py", "wardrobe.py")


def _newest_source_mtime() -> float:
    """When the code on disk last changed, so the page can tell it's talking to
    a process started before an edit — a stale server serving old routes and
    old bugs is indistinguishable from the new code simply not working."""
    from config import PROJECT_ROOT

    times = []
    for name in _SOURCE_FILES:
        path = PROJECT_ROOT / name
        if path.exists():
            times.append(path.stat().st_mtime)
    return max(times, default=0)


# ---------- Background poller ----------
# Picks up ChatGPT's finished images and, if drip scheduling is on, keeps the
# queue fed — both while this process is running. Zernio still fires each
# scheduled post from its own servers, so a post already scheduled goes out
# even if this app is closed afterwards; only *picking up new work* needs it
# running.
POLL_SECONDS = 60


def _poll_forever():
    while True:
        try:
            try:
                board_names = [b["name"] for b in get_boards()]
            except Exception:
                board_names = None
            generated_pins.sync_incoming_images(board_names=board_names)
            generated_pins.run_drip_schedule()
        except Exception as e:
            print(f"[poller] {e}")
        time.sleep(POLL_SECONDS)


def _start_background_poller():
    threading.Thread(target=_poll_forever, daemon=True).start()


@app.get("/api/boards")
def api_boards():
    try:
        return jsonify(get_boards(force=request.args.get("refresh") == "1"))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


if __name__ == "__main__":
    # Auto-reload on edit. Without it a code change needs a manual restart, and
    # a stale process quietly serving old code looks exactly like the new code
    # being broken. Set PIN_STUDIO_NO_RELOAD=1 to pin the running version.
    use_reloader = os.getenv("PIN_STUDIO_NO_RELOAD") != "1"

    # The reloader runs this file in two processes: a watcher and the child
    # actually serving. Only the child should poll, or every scheduled pin gets
    # picked up twice.
    if not use_reloader or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        _start_background_poller()

    print("Pin studio running at http://localhost:5000")
    app.run(port=5000, debug=False, use_reloader=use_reloader)
