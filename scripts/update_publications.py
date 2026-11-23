#!/usr/bin/env python3
"""Download Francisco S. Melo's publication list from DBLP and save it as publications.json.

The data comes from DBLP's SPARQL service (sparql.dblp.org). DBLP's website and its export URLs
(e.g. dblp.org/pid/86/839.xml) are behind bot protection that blocks automated downloads; the
SPARQL service, also run by DBLP, holds the same records and is open to scripts.

Run weekly by .github/workflows/update-publications.yml. Uses only the Python standard library.

For each paper, the page shows a [PDF] link when a PDF is available, otherwise a [Publisher] link.
A PDF is looked for in this order:
  1. a local copy in pdfs/, named after the paper's DBLP key with "/" replaced by "-"
     (for example pdfs/conf-iros-MeloV15.pdf);
  2. a free, legal copy found through OpenAlex (open-access data from Unpaywall);
  3. the paper's arXiv version, if DBLP lists one;
  4. a direct PDF link from an open-access venue (PMLR, NeurIPS, IJCAI, OpenReview, ...).

BibTeX entries already in publications.json are kept as they are; new papers get an entry built
from DBLP's data in DBLP's format, with a citation key in the style of the old fmelo.bib.

If DBLP can't be reached, the script exits with an error and leaves publications.json untouched,
so the website keeps showing the last good list. If OpenAlex can't be reached, the list is still
updated, using the other PDF sources.
"""
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PID = "86/839"
SPARQL = "https://sparql.dblp.org/sparql"
SITE = Path(__file__).resolve().parent.parent
OUT = SITE / "publications.json"
PDF_DIR = SITE / "pdfs"
HEADERS = {
    "User-Agent": "facsmelo.github.io publication list updater (weekly; +https://facsmelo.github.io)",
}

PREFIXES = ("PREFIX dblp: <https://dblp.org/rdf/schema#> "
            "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> ")
ME = f"<https://dblp.org/pid/{PID}>"
QUERIES = {
    # One row per (publication, property, value).
    "pubs": f"""SELECT ?pub ?type ?p ?o WHERE {{ ?pub dblp:createdBy {ME} . ?pub rdf:type ?type .
        FILTER(?type != dblp:Publication)
        VALUES ?p {{ dblp:title dblp:yearOfPublication dblp:yearOfEvent dblp:publishedIn dblp:publishedInJournal
                    dblp:publishedInJournalVolume dblp:publishedInJournalVolumeIssue dblp:publishedInBook
                    dblp:pagination dblp:doi dblp:primaryDocumentPage dblp:documentPage dblp:archivedWebpage dblp:publishedAsPartOf
                    dblp:publishedBy dblp:publishedInSeries dblp:publishedInSeriesVolume dblp:isbn }}
        ?pub ?p ?o }}""",
    # Authors and editors, in order.
    "sigs": f"""SELECT ?pub ?kind ?ord ?name ?creator WHERE {{ ?pub dblp:createdBy {ME} .
        ?pub dblp:hasSignature ?s . ?s dblp:signatureOrdinal ?ord ; dblp:signatureDblpName ?name ;
        dblp:signatureCreator ?creator ; rdf:type ?kind . FILTER(?kind != dblp:Signature) }}""",
    # The proceedings or book a paper appeared in (for full venue names in BibTeX).
    "parents": f"""SELECT ?parent ?p ?o WHERE {{ ?pub dblp:createdBy {ME} ; dblp:publishedAsPartOf ?parent .
        VALUES ?p {{ dblp:title dblp:publishedBy dblp:publishedInSeries dblp:publishedInSeriesVolume
                    dblp:yearOfPublication dblp:isbn }} ?parent ?p ?o }}""",
    "parentEds": f"""SELECT ?parent ?ord ?name WHERE {{ ?pub dblp:createdBy {ME} ; dblp:publishedAsPartOf ?parent .
        ?parent dblp:hasSignature ?s . ?s a dblp:EditorSignature ; dblp:signatureOrdinal ?ord ;
        dblp:signatureDblpName ?name }}""",
}


def get(url, timeout=60, accept=None):
    headers = dict(HEADERS, **({"Accept": accept} if accept else {}))
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def sparql(query):
    url = SPARQL + "?query=" + urllib.parse.quote(PREFIXES + query)
    data = json.loads(get(url, timeout=120, accept="application/sparql-results+json"))
    return [{k: v["value"] for k, v in row.items()} for row in data["results"]["bindings"]]


def fetch_dblp():
    """Run the SPARQL queries. Exits with an error (leaving publications.json untouched) if DBLP fails."""
    errors = []
    for attempt in range(3):
        try:
            return {name: sparql(q) for name, q in QUERIES.items()}
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
            time.sleep(30)
    sys.exit("Could not get DBLP data from its SPARQL service:\n  " + "\n  ".join(errors))


SCHEMA = "https://dblp.org/rdf/schema#"
REC = "https://dblp.org/rec/"


def build_records(raw):
    """Turn the SPARQL rows into one record per publication."""
    recs = {}
    for row in raw["pubs"]:
        r = recs.setdefault(row["pub"], {"props": {}})
        r["type"] = row["type"].replace(SCHEMA, "")
        r["props"].setdefault(row["p"].replace(SCHEMA, ""), []).append(row["o"])
    people = {}
    for row in raw["sigs"]:
        kind = "editors" if "Editor" in row["kind"] else "authors"
        people.setdefault((row["pub"], kind), []).append((int(row["ord"]), row["name"], row["creator"]))
    parents = {}
    for row in raw["parents"]:
        parents.setdefault(row["parent"], {}).setdefault(row["p"].replace(SCHEMA, ""), []).append(row["o"])
    for row in raw["parentEds"]:
        parents.setdefault(row["parent"], {}).setdefault("_editors", []).append((int(row["ord"]), row["name"]))

    out = []
    for iri, r in recs.items():
        p = r["props"]
        one = lambda k: (p.get(k) or [""])[0]  # noqa: E731
        primary = one("primaryDocumentPage")
        links = ([primary] if primary else []) + [u for u in sorted(p.get("documentPage", [])) if u != primary]
        links = links or sorted(p.get("archivedWebpage", []))
        parent_iri = one("publishedAsPartOf")
        parent = parents.get(parent_iri, {})
        out.append({
            "key": iri.replace(REC, ""),
            "type": r["type"],
            "title": re.sub(r"\.$", "", one("title")),
            # For conference papers DBLP lists the year of the event (as its website does), not of printing.
            "year": one("yearOfEvent") or one("yearOfPublication"),
            "journal": one("publishedInJournal"),
            "book": one("publishedInBook") or one("publishedIn"),
            "volume": one("publishedInJournalVolume") or one("publishedInSeriesVolume"),
            "number": one("publishedInJournalVolumeIssue"),
            "pages": one("pagination"),
            "publisher": one("publishedBy"),
            "series": one("publishedInSeries"),
            "isbn": one("isbn"),
            "doi": one("doi").split("doi.org/", 1)[-1].lower() if one("doi") else "",
            "links": links,
            "authors": [(n, c.replace("https://dblp.org/pid/", "")) for _, n, c in sorted(people.get((iri, "authors"), []))],
            "editors": [(n, c.replace("https://dblp.org/pid/", "")) for _, n, c in sorted(people.get((iri, "editors"), []))],
            "parent": {
                "title": (parent.get("title") or [""])[0],
                "publisher": (parent.get("publishedBy") or [""])[0],
                "series": (parent.get("publishedInSeries") or [""])[0],
                "volume": (parent.get("publishedInSeriesVolume") or [""])[0],
                "editors": [n for _, n in sorted(parent.get("_editors", []))],
            } if parent_iri else None,
        })
    return out


ACCENTS = {"\u0301": "'", "\u0300": "`", "\u0302": "^", "\u0303": "~", "\u0308": '"', "\u0327": "c",
           "\u030c": "v", "\u030a": "r", "\u0304": "=", "\u0306": "u", "\u0307": ".", "\u030b": "H"}
SPECIAL = {"&": r"\&", "%": r"\%", "#": r"\#", "_": r"\_", "ß": r"{\ss}", "ø": r"{\o}", "Ø": r"{\O}",
           "æ": r"{\ae}", "Æ": r"{\AE}", "ł": r"{\l}", "Ł": r"{\L}", "ı": r"{\i}", "–": "--", "—": "---"}


def latex(text):
    r"""Write accented letters the way DBLP's BibTeX does, e.g. "ã" -> {\~{a}}, "í" -> {\'{\i}}."""
    out = []
    for ch in unicodedata.normalize("NFD", text):
        if ch in ACCENTS and out:
            base = out.pop()
            base = {"i": r"\i", "j": r"\j"}.get(base, base)
            out.append("{\\" + ACCENTS[ch] + "{" + base + "}}")
        elif unicodedata.combining(ch):
            continue
        else:
            out.append(SPECIAL.get(ch, ch))
    return unicodedata.normalize("NFC", "".join(out))


def protect(text):
    """Brace acronyms (AAMAS, ACM, ...) so BibTeX styles keep them in capitals, as DBLP does."""
    return re.sub(r"(?<![\\{\w])([A-Z][A-Z0-9]*[A-Z][A-Z0-9]*)(?![\w}])", r"{\1}", text)


def make_bibtex(rec, cite):
    """A BibTeX entry in DBLP's layout, built from the SPARQL record."""
    t, par = rec["type"], rec["parent"] or {}
    names = lambda lst: " and\n                  ".join(latex(clean_name(n)) for n in lst)  # noqa: E731
    fields = []
    if rec["authors"]:
        fields.append(("author", names(n for n, _ in rec["authors"])))
    if t in ("Inproceedings", "Incollection") and par.get("editors"):
        fields.append(("editor", names(par["editors"])))
    if t == "Editorship" and rec["editors"]:
        fields.append(("editor", names(n for n, _ in rec["editors"])))
    fields.append(("title", protect(latex(rec["title"]))))
    if t in ("Article", "Informal"):
        etype = "article"
        fields += [("journal", protect(latex(rec["journal"] or rec["book"]))), ("volume", rec["volume"]), ("number", rec["number"])]
    elif t == "Inproceedings":
        etype = "inproceedings"
        fields += [("booktitle", protect(latex(par.get("title") or rec["book"]))), ("series", latex(par.get("series", ""))),
                   ("volume", par.get("volume", ""))]
    elif t == "Incollection":
        etype = "incollection"
        fields += [("booktitle", protect(latex(par.get("title") or rec["book"]))), ("series", latex(par.get("series", ""))),
                   ("volume", par.get("volume", ""))]
    elif t == "Editorship":
        etype = "proceedings"
        fields += [("series", latex(rec["series"])), ("volume", rec["volume"])]
    else:
        etype = "misc"
    fields += [("pages", rec["pages"].replace("-", "--")),
               ("publisher", protect(latex(par.get("publisher") or rec["publisher"]))),
               ("year", rec["year"]), ("url", rec["links"][0] if rec["links"] else ""), ("doi", rec["doi"])]
    body = ",\n".join(f"  {k:<12} = {{{v}}}" for k, v in fields if v)
    return f"@{etype}{{{cite},\n{body}\n}}"


# Citation keys follow the style of the old fmelo.bib: first author's surname + 2-digit year + venue code,
# e.g. melo11ai, baraka17icsr. Keys from the old file are kept as they were (scripts/bibkeys.json), and a
# key, once assigned, is never changed (it is carried over from the previous publications.json).
VENUE_CODES = {
    "conf/atal": "aamas", "conf/ifaamas": "aamas", "conf/nips": "neurips", "conf/aaaifs": "aaai", "conf/aips": "icaps", "conf/digra": "fdg",
    "conf/eumas": "mas", "conf/icdl-epirob": "icdl", "conf/icgi2": "icgi", "conf/pkdd": "ecml",
    "conf/ro-man": "roman", "conf/socrob": "icsr", "journals/aamas": "jaamas", "journals/adb": "ab",
    "journals/arobots": "ar", "journals/artmed": "aiim", "journals/paladyn": "jbr", "journals/corr": "arxiv",
}


def venue_code(dblp_key):
    prefix = dblp_key.rsplit("/", 1)[0]
    if prefix in VENUE_CODES:
        return VENUE_CODES[prefix]
    if prefix.startswith("series/"):
        return "chap"
    return re.sub(r"[^a-z0-9]", "", prefix.split("/")[-1].lower())


def surname(name):
    last = name.split()[-1] if name.split() else "anon"
    ascii_ = unicodedata.normalize("NFKD", last).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", ascii_.lower()) or "anon"


def assign_citekeys(pubs, previous):
    seed_file = Path(__file__).resolve().parent / "bibkeys.json"
    known = json.loads(seed_file.read_text(encoding="utf-8")) if seed_file.exists() else {}
    known.update(previous)
    taken = set(known.values())
    for p in pubs:
        if p["key"] in known:
            p["cite"] = known[p["key"]]
            continue
        first = p["authors"][0]["name"] if p["authors"] else "anon"
        base = f"{surname(first)}{p['year'][-2:]}{venue_code(p['key'])}"
        cite = base
        if base in taken or base + "-a" in taken:
            cite = next(f"{base}-{c}" for c in "bcdefghijklmnopqrstuvwxyz" if f"{base}-{c}" not in taken)
        taken.add(cite)
        p["cite"] = cite


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


def local_pdf_name(key):
    return key.replace("/", "-") + ".pdf"


def parse(raw, oa_lookup=fetch_open_access_pdfs):
    pubs, arxiv, recs = [], {}, {}
    for rec in build_records(raw):
        t = rec["type"]
        arxiv_id = ""
        if t == "Article":
            kind, venue = "Journal", rec["journal"]
        elif t == "Informal":
            kind, venue = "Preprint", rec["journal"] or rec["book"]
            if venue == "CoRR":
                venue, arxiv_id = "arXiv", rec["volume"].replace("abs/", "")
        elif t == "Inproceedings":
            kind, venue = "Conference", rec["book"]
        elif t == "Incollection":
            kind, venue = "Chapter", rec["book"]
        elif t == "Editorship":
            kind, venue = "Edited", rec["publisher"] or rec["series"]
        elif t == "Book":
            kind, venue = "Book", rec["publisher"]
        else:
            continue  # withdrawn entries, data sets, software, ...

        if arxiv_id:
            arxiv.setdefault(norm(rec["title"]), arxiv_id)
        is_edited = kind == "Edited" and not rec["authors"]
        people = rec["editors"] if is_edited else rec["authors"]
        recs[rec["key"]] = rec
        pubs.append({
            "key": rec["key"],
            "title": rec["title"],
            "authors": [{"name": clean_name(n), "me": pid == PID} for n, pid in people],
            "editors": is_edited,
            "venue": venue,
            "year": rec["year"],
            "type": kind,
            "url": rec["links"][0] if rec["links"] else "",
            "_doi": rec["doi"],
            "_links": rec["links"],
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
    return pubs, recs


def main():
    pubs, recs = parse(fetch_dblp())
    if len(pubs) < 20:  # a sanity check against a broken or truncated download
        sys.exit(f"Only {len(pubs)} publications parsed; refusing to overwrite {OUT.name}.")

    old = json.loads(OUT.read_text(encoding="utf-8")).get("publications", []) if OUT.exists() else []
    old_bibs = {p["key"]: p["bib"] for p in old if p.get("bib")}
    # Process older papers first, so that when two papers share a key, the older one keeps the plain key.
    assign_citekeys(sorted(pubs, key=lambda p: (p["year"], p["key"])),
                    {p["key"]: p["cite"] for p in old if p.get("cite")})
    for p in pubs:
        p["bib"] = old_bibs.get(p["key"]) or make_bibtex(recs[p["key"]], p["cite"])

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
