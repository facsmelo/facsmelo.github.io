#!/usr/bin/env python3
"""Download Francisco S. Melo's publication list from DBLP and save it as publications.json.

Run weekly by .github/workflows/update-publications.yml. Uses only the Python standard library.

For each paper, the page shows a [PDF] link when a PDF is available, otherwise a [Publisher] link.
A PDF is looked for in this order:
  1. a local copy in pdfs/, named after the paper's DBLP key with "/" replaced by "-"
     (for example pdfs/conf-iros-MeloV15.pdf);
  2. a free, legal copy found through OpenAlex (open-access data from Unpaywall);
  3. the paper's arXiv version, if DBLP lists one;
  4. a direct PDF link from an open-access venue (PMLR, NeurIPS, IJCAI, OpenReview, ...).

If DBLP can't be reached, the script exits with an error and leaves publications.json untouched,
so the website keeps showing the last good list. If OpenAlex can't be reached, the list is still
updated, using the other PDF sources.
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

PID = "86/839"
SOURCES = [
    f"https://dblp.org/pid/{PID}.xml",
    f"https://dblp.uni-trier.de/pid/{PID}.xml",
]
SITE = Path(__file__).resolve().parent.parent
OUT = SITE / "publications.json"
PDF_DIR = SITE / "pdfs"
HEADERS = {
    "User-Agent": "facsmelo.github.io publication list updater (weekly; +https://facsmelo.github.io)",
}


def get(url, timeout=60):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def fetch_dblp():
    errors = []
    for attempt in range(3):
        for url in SOURCES:
            try:
                text = get(url)
                if "<dblpperson" in text[:2000]:
                    return text
                errors.append(f"{url}: unexpected response (not DBLP XML): {text[:120]!r}")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{url}: {e}")
        time.sleep(20)
    sys.exit("Could not download DBLP data:\n  " + "\n  ".join(errors))


def fetch_bibtex():
    """DBLP's standard BibTeX for all publications (one request). Returns {dblp_key: entry}, or None on failure."""
    for url in (f"https://dblp.org/pid/{PID}.bib?param=1", f"https://dblp.uni-trier.de/pid/{PID}.bib?param=1"):
        try:
            text = get(url)
            if "@" in text[:500] and "DBLP:" in text[:500]:
                return split_bibtex(text)
        except Exception as e:  # noqa: BLE001
            print(f"BibTeX download failed from {url}: {e}")
    return None


def split_bibtex(text):
    entries = {}
    for m in re.finditer(r"^@\w+\{DBLP:([^,]+),\n.*?^\}$", text, re.M | re.S):
        entry = "\n".join(line for line in m.group(0).splitlines()
                          if not re.match(r"\s*(timestamp|biburl|bibsource)\s*=", line))
        entries[m.group(1)] = entry
    return entries


def fetch_open_access_pdfs(dois):
    """Map lower-case DOI -> URL of a free PDF, using OpenAlex. Returns {} if OpenAlex is unreachable."""
    found = {}
    dois = sorted(set(dois))
    for i in range(0, len(dois), 40):
        chunk = "|".join("https://doi.org/" + d for d in dois[i:i + 40])
        url = ("https://api.openalex.org/works?per-page=50&select=doi,best_oa_location&filter=doi:"
               + urllib.parse.quote(chunk, safe=":/|"))
        try:
            data = json.loads(get(url))
        except Exception as e:  # noqa: BLE001
            print(f"OpenAlex lookup failed, continuing without it: {e}")
            return found
        for w in data.get("results", []):
            loc = w.get("best_oa_location") or {}
            if w.get("doi") and loc.get("pdf_url"):
                found[w["doi"].replace("https://doi.org/", "").lower()] = loc["pdf_url"]
        time.sleep(1)
    return found


def direct_pdf(url):
    """Turn a link to an open-access venue's paper page into a direct PDF link, when the pattern is known."""
    if not url:
        return ""
    if url.lower().endswith(".pdf"):
        return url
    m = re.match(r"(https?://proceedings\.mlr\.press/v\d+/)([^/]+)\.html$", url)
    if m:
        return f"{m.group(1)}{m.group(2)}/{m.group(2)}.pdf"
    m = re.match(r"(https?://(?:proceedings\.neurips\.cc|papers\.nips\.cc|proceedings\.iclr\.cc)/.*)/hash/(.+)-Abstract(.*)\.html$", url)
    if m:
        return f"{m.group(1)}/file/{m.group(2)}-Paper{m.group(3)}.pdf"
    m = re.match(r"https?://(?:www\.)?ijcai\.org/proceedings/(\d{4})/(\d+)/?$", url)
    if m:
        return f"https://www.ijcai.org/proceedings/{m.group(1)}/{m.group(2)}.pdf"
    m = re.match(r"https?://openreview\.net/forum\?id=(.+)$", url)
    if m:
        return f"https://openreview.net/pdf?id={m.group(1)}"
    m = re.match(r"(https?://aclanthology\.org/[^/]+?)/?$", url)
    if m:
        return m.group(1) + ".pdf"
    return ""


def clean_name(name):
    return re.sub(r"\s+\d{4}$", "", name.strip())  # "Ana Paiva 0001" -> "Ana Paiva"


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def text_of(el):
    return "".join(el.itertext()).strip() if el is not None else ""


def local_pdf_name(key):
    return key.replace("/", "-") + ".pdf"


def parse(xml_text, oa_lookup=fetch_open_access_pdfs):
    root = ET.fromstring(xml_text)
    pubs, arxiv = [], {}
    for r in root.findall("r"):
        if len(r) == 0:
            continue
        e = r[0]
        tag, publtype = e.tag, e.get("publtype", "")
        if publtype in ("withdrawn", "data", "software"):
            continue
        arxiv_id = ""
        if tag == "article":
            kind = "Preprint" if publtype == "informal" else "Journal"
            venue = text_of(e.find("journal"))
            if venue == "CoRR":
                kind, venue = "Preprint", "arXiv"
                arxiv_id = text_of(e.find("volume")).replace("abs/", "")
        elif tag == "inproceedings":
            kind, venue = "Conference", text_of(e.find("booktitle"))
        elif tag == "incollection":
            kind, venue = "Chapter", text_of(e.find("booktitle"))
        elif tag in ("proceedings", "book"):
            kind = "Edited" if e.find("editor") is not None and e.find("author") is None else "Book"
            venue = text_of(e.find("publisher")) or text_of(e.find("series"))
        else:
            kind, venue = "Other", text_of(e.find("journal")) or text_of(e.find("booktitle"))

        title = re.sub(r"\.$", "", text_of(e.find("title")))
        if arxiv_id:
            arxiv.setdefault(norm(title), arxiv_id)
        links = [text_of(x) for x in e.findall("ee")]
        doi = next((u.split("doi.org/", 1)[1].lower() for u in links if "doi.org/" in u), "")
        people = e.findall("author") or e.findall("editor")
        pubs.append({
            "key": e.get("key"),
            "title": title,
            "authors": [{"name": clean_name(text_of(p)), "me": p.get("pid") == PID} for p in people],
            "editors": e.find("author") is None and e.find("editor") is not None,
            "venue": venue,
            "year": text_of(e.find("year")),
            "type": kind,
            "url": links[0] if links else "",
            "_doi": doi,
            "_links": links,
            "_arxiv": arxiv_id,
        })

    # Drop arXiv versions of papers that DBLP also lists as published (their PDF is reused below).
    published = {norm(p["title"]) for p in pubs if p["type"] != "Preprint"}
    pubs = [p for p in pubs if not (p["type"] == "Preprint" and norm(p["title"]) in published)]

    needs_lookup = [p["_doi"] for p in pubs if p["_doi"] and not (PDF_DIR / local_pdf_name(p["key"])).exists()]
    oa = oa_lookup(needs_lookup) if needs_lookup else {}

    for p in pubs:
        local = PDF_DIR / local_pdf_name(p["key"])
        twin = p["_arxiv"] or arxiv.get(norm(p["title"]), "")
        if local.exists():
            pdf = "pdfs/" + local.name
        elif p["_doi"] in oa:
            pdf = oa[p["_doi"]]
        elif twin:
            pdf = f"https://arxiv.org/pdf/{twin}"
        else:
            pdf = next((d for d in map(direct_pdf, p["_links"]) if d), "")
        p["pdf"] = pdf
        for k in ("_doi", "_links", "_arxiv"):
            del p[k]

    pubs.sort(key=lambda p: p["year"], reverse=True)
    return pubs


def main():
    pubs = parse(fetch_dblp())
    if len(pubs) < 20:  # a sanity check against a broken or truncated download
        sys.exit(f"Only {len(pubs)} publications parsed; refusing to overwrite {OUT.name}.")

    # Attach a BibTeX entry to each paper; if DBLP's BibTeX export fails, keep last week's entries.
    bibs = fetch_bibtex()
    if bibs is None and OUT.exists():
        old = json.loads(OUT.read_text(encoding="utf-8")).get("publications", [])
        bibs = {p["key"]: p["bib"] for p in old if p.get("bib")}
    for p in pubs:
        p["bib"] = (bibs or {}).get(p["key"], "")
    data = {
        "source": f"https://dblp.org/pid/{PID}.html",
        "checked": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "publications": pubs,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    with_pdf = sum(1 for p in pubs if p["pdf"])
    print(f"Wrote {len(pubs)} publications to {OUT} ({with_pdf} with a PDF link)")


if __name__ == "__main__":
    main()
