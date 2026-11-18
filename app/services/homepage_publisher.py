"""Publish an explicit public-file allowlist. No event originals or credentials."""

import html
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

import requests

from app.core.firebase_config import PROJECT_ROOT
from app.services.homepage_content import IMAGE_PATTERN, RENDITION_ROOT, HomepageError, public_configuration

PUBLIC_FILES = (
    "index.html", "main.js", "relay-search.mjs", "selection-csv.mjs", "style.css", "navigation.css",
    "photo-archive.mjs", "account.html", "account.js", "account.css", "client-event-gallery.js",
    "firebase-client.js", "firebase-config.js", "build-config.mjs", "gallery.html", "gallery.js", "gallery.css",
    "favicon.svg", "favicon.webp", "camera-aperture.svg", "camera-aperture-static.svg", "sm-photography-logo.png",
    "vercel.json", "package.json", "find-photos.html", "homepage.css", "homepage.js", "homepage-content.mjs",
    "homepage-portfolio.json", "legacy-photo-route.js", "enquiry.mjs", "admin.html", "homepage-admin.js", "homepage-admin.css",
)
SAMPLE_FILES = ("sample-bride.webp", "sample-wedding.webp", "sample-celebration.webp", "sample-together.webp", "credits.json")
LOG = logging.getLogger(__name__)


def render_homepage(source, config):
    for element_id, photo in (("hero-photo", config["photos"][0]), ("detail-photo", config["photos"][1])):
        pattern = r'<img\b(?=[^>]*\bid="' + element_id + r'")[^>]*>'
        def replace(match):
            tag = re.sub(r'\bsrc="[^"]*"', lambda _: 'src="' + html.escape(photo["image"], quote=True) + '"', match[0])
            return re.sub(r'\balt="[^"]*"', lambda _: 'alt="' + html.escape(photo["alt"], quote=True) + '"', tag)
        source, count = re.subn(pattern, replace, source)
        if count != 1:
            raise HomepageError("The homepage template needs attention before publishing.", 503)
    for element_id, visible in (("hero-sample-label", config["photos"][0]["sample"]),
                                ("sample-note", any(p["sample"] for p in config["photos"])),
                                ("sample-credits", any(p["sample"] for p in config["photos"]))):
        pattern = r'<(?:figcaption|p|details)\b(?=[^>]*\bid="' + element_id + r'")[^>]*>'
        def toggle(match):
            tag = re.sub(r"\s+hidden(?:=\"[^\"]*\")?", "", match[0])
            return tag if visible else tag[:-1] + " hidden>"
        source = re.sub(pattern, toggle, source)
    return source


def prepare_public_stage(source, stage, config, rendition_root=None):
    config = public_configuration(config)
    source, stage = Path(source).resolve(), Path(stage).resolve()
    destination = stage / "online"
    destination.mkdir(parents=True, exist_ok=True)
    for name in PUBLIC_FILES:
        path = (source / name).resolve()
        if not path.is_relative_to(source) or path.is_symlink() or not path.is_file():
            raise HomepageError("A required public website file is missing.", 503)
        shutil.copyfile(path, destination / name)
    for name in SAMPLE_FILES:
        target = destination / "assets/home" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / "assets/home" / name, target)
    for photo in config["photos"]:
        relative = photo["image"]
        if not IMAGE_PATTERN.fullmatch(relative):
            raise HomepageError("A homepage image path is invalid.")
        asset_root = Path(rendition_root or RENDITION_ROOT).resolve() if relative.startswith("./assets/home/published/") else source / "assets/home"
        path = asset_root / Path(relative).name
        if not path.resolve().is_relative_to(asset_root) or path.is_symlink() or not path.is_file():
            raise HomepageError("A saved homepage photo is unavailable on the studio computer.", 503)
        target = destination / relative[2:]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    manifest = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    (destination / "homepage-portfolio.json").write_text(manifest, encoding="utf-8")
    rendered = render_homepage((source / "index.html").read_text(encoding="utf-8"), config)
    (destination / "index.html").write_text(rendered, encoding="utf-8")
    project = json.loads((source / ".vercel/project.json").read_text(encoding="utf-8"))
    if project.get("projectId") != "prj_Dn46VKXlRXUoA1Q8HSMni2YUc9vh" or project.get("orgId") != "team_equFQ6FROuzopn7cDCLe1u7V":
        raise HomepageError("The website publisher is linked to a different Vercel project.", 503)
    (stage / ".vercel").mkdir(exist_ok=True)
    (stage / ".vercel/project.json").write_text(json.dumps(project), encoding="utf-8")
    shutil.copyfile(source / "vercel.json", stage / "vercel.json")
    (stage / ".vercelignore").write_text("*.log\n*.result.json\n", encoding="utf-8")
    return rendered, manifest


def cli_command():
    launcher, node = shutil.which("vercel"), shutil.which("node")
    entry = Path(launcher).parent / "node_modules/vercel/dist/index.js" if launcher else None
    if not node or not entry or not entry.is_file():
        raise HomepageError("The website publisher needs the existing Vercel CLI on the studio computer.", 503)
    return [node, str(entry)]


def publish_homepage(config, progress):
    source = PROJECT_ROOT / "online"
    root = PROJECT_ROOT / ".phase2-deps"
    root.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="homepage-publish-", dir=root)).resolve()
    command = cli_command()

    def run(arguments, cwd=stage, timeout=240):
        result = subprocess.run(command + arguments, cwd=cwd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode:
            LOG.warning("Homepage publisher command failed; exit code %d", result.returncode)
            raise HomepageError("Publishing could not complete. Check Vercel sign-in on the studio computer and try again.", 503)
        return result.stdout

    try:
        rendered, manifest = prepare_public_stage(source, stage, config)
        progress("Uploading the approved homepage photos…")
        result = json.loads(run(["deploy", "--prod", "--skip-domain", "--yes", "--scope", "smblog", "--format=json", "--archive=tgz"]))
        deployment = result.get("deployment", {})
        url = deployment.get("url", "")
        if (deployment.get("readyState") != "READY" or deployment.get("target") != "production"
                or not re.fullmatch(r"https://ai-photo-k5fu-[a-z0-9]+-smblog\.vercel\.app", url)):
            raise HomepageError("The new homepage build was not ready to publish.", 503)
        progress("Checking the new homepage before it goes live…")
        for path, expected in (("index.html", rendered), ("homepage-portfolio.json", manifest)):
            output = stage / ("verified-" + path)
            status = run(["curl", "/" + path, "--deployment", url, "--", "--silent", "--show-error", "--location",
                          "--output", str(output), "--write-out", "%{http_code}"], cwd=source, timeout=60).strip()
            if status != "200" or output.read_bytes() != expected.encode("utf-8"):
                raise HomepageError("The new homepage could not be verified. Please publish again.", 503)
        progress("Making your updated homepage live…")
        try:
            run(["promote", url, "--yes", "--scope", "smblog"], cwd=source, timeout=180)
        except (subprocess.TimeoutExpired, HomepageError):
            # A lost CLI response does not mean promotion failed; verify the actual live file.
            pass
        for attempt in range(5):
            try:
                response = requests.get("https://sm-photo-studio.vercel.app/homepage-portfolio.json",
                                        headers={"Cache-Control": "no-cache"}, timeout=(5, 15))
                if response.status_code == 200 and response.content == manifest.encode("utf-8"):
                    break
            except requests.RequestException:
                pass
            if attempt == 4:
                raise HomepageError("Publishing could not be confirmed. Check the homepage, then try Publish again.", 503)
            time.sleep(2)
        # Keep the local public source in agreement with the verified live portfolio.
        for name, data in (("index.html", rendered), ("homepage-portfolio.json", manifest)):
            temporary = source / ("." + name + ".publishing")
            temporary.write_text(data, encoding="utf-8")
            os.replace(temporary, source / name)
    finally:
        # Only this publisher's checked temporary directory is eligible for recursive removal.
        if stage != root.resolve() and stage.is_relative_to(root.resolve()) and stage.name.startswith("homepage-publish-"):
            shutil.rmtree(stage, ignore_errors=True)
