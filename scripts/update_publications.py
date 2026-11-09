#!/usr/bin/env python3
"""Download Francisco S. Melo's publication list from DBLP and save it as publications.json.

Run weekly by .github/workflows/update-publications.yml. Uses only the Python standard library.
If DBLP can't be reached or returns something unexpected, the script exits with an error and
leaves the existing publications.json untouched, so the website keeps showing the last good list.
"""
import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

PID = "86/839"
SOURCES = [
    f"https://dblp.org/pid/{PID}.xml",
    f"https://dblp.uni-trier.de/pid/{PID}.xml",
]
OUT = Path(__file__).resolve().parent.parent / "publications.json"
HEADERS = {
    "User-Agent": "facsmelo.github.io publication list updater (weekly; +https://facsmelo.github.io)",
    "Accept": "application/xml",
}


def fetch():
    errors = []
    for attempt in range(3):
        for url in SOURCES:
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=60) as r:
                    text = r.read().decode("utf-8")
                if "<dblpperson" in text[:2000]:
                    return text
                errors.append(f"{url}: unexpected response (not DBLP XML): {text[:120]!r}")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{url}: {e}")
        time.sleep(20)
    sys.exit("Could not download DBLP data:\n  " + "\n  ".join(errors))


def clean_name(name):
    return re.sub(r"\s+\d{4}$", "", name.strip())  # "Ana Paiva 0001" -> "Ana Paiva"


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def text_of(el):
    return "".join(el.itertext()).strip() if el is not None else ""


def parse(xml_text):
    root = ET.fromstring(xml_text)
    pubs = []
    for r in root.findall("r"):
        if len(r) == 0:
            continue
        e = r[0]
        tag, publtype = e.tag, e.get("publtype", "")
        if publtype in ("withdrawn", "data", "software"):
            continue
        if tag == "article":
            kind = "Preprint" if publtype == "informal" else "Journal"
            venue = text_of(e.find("journal"))
            if venue == "CoRR":
                kind, venue = "Preprint", "arXiv"
        elif tag == "inproceedings":
            kind, venue = "Conference", text_of(e.find("booktitle"))
        elif tag == "incollection":
            kind, venue = "Chapter", text_of(e.find("booktitle"))
        elif tag in ("proceedings", "book"):
            kind = "Edited" if e.find("editor") is not None and e.find("author") is None else "Book"
            venue = text_of(e.find("publisher")) or text_of(e.find("series"))
        else:
            kind, venue = "Other", text_of(e.find("journal")) or text_of(e.find("booktitle"))

        people = e.findall("author") or e.findall("editor")
        authors = [{"name": clean_name(text_of(p)), "me": p.get("pid") == PID} for p in people]
        ee = e.find("ee")
        pubs.append({
            "title": re.sub(r"\.$", "", text_of(e.find("title"))),
            "authors": authors,
            "editors": e.find("author") is None and e.find("editor") is not None,
            "venue": venue,
            "year": text_of(e.find("year")),
            "type": kind,
            "url": text_of(ee),
            "dblp": f"https://dblp.org/rec/{e.get('key')}.html",
        })

    # Drop arXiv versions of papers that DBLP also lists as published.
    published = {norm(p["title"]) for p in pubs if p["type"] != "Preprint"}
    pubs = [p for p in pubs if not (p["type"] == "Preprint" and norm(p["title"]) in published)]
    pubs.sort(key=lambda p: p["year"], reverse=True)
    return pubs


def main():
    pubs = parse(fetch())
    if len(pubs) < 20:  # a sanity check against a broken or truncated download
        sys.exit(f"Only {len(pubs)} publications parsed; refusing to overwrite {OUT.name}.")
    data = {
        "source": f"https://dblp.org/pid/{PID}.html",
        "checked": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "publications": pubs,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {len(pubs)} publications to {OUT}")


if __name__ == "__main__":
    main()
