#!/usr/bin/env python3
"""
Baut die GitHub-Pages-Website aus zwei Sorten von Textdateien:

- inhalt.txt (im Repo-Root): Name, Rolle, About-Text, Skills und
  Kontakt-Links für index.html (die Startseite).
- projekte/<name>/projekt.txt: alle Texte, Überschriften, Links und Bilder
  EINES Projekts (Titel, Tagline, Tech-Stack, Repo/Live-Links, die
  Beschreibung mit ###-Überschriften und die Bildergalerie).

Für Änderungen an Texten/Links/Bildern reicht es also, die jeweilige
projekt.txt bzw. inhalt.txt zu bearbeiten und dieses Skript erneut
laufen zu lassen — index.html und die Projektseiten werden daraus neu erzeugt.
Alles im Repo ist reines HTML/CSS/JS — kein Build-Schritt, direkt als
GitHub-Pages-Root deploybar (index.html ist bewusst der Dateiname, den
GitHub Pages automatisch als Startseite ausliefert).

###-Headlines (auch ohne Leerzeichen: ###Foo) werden als
<h2 class="section-title">...</h2> gerendert.
"""
import base64
import datetime
import json
import math
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).parent / "projekte"
PORTFOLIO = Path(__file__).parent / "index.html"
ALLE_PROJEKTE = Path(__file__).parent / "alle-projekte.html"
SITE_CONTENT = Path(__file__).parent / "inhalt.txt"
PROJECT_FILE_NAME = "projekt.txt"

# ===== Mehrsprachigkeit =====
# Deutsch ist die Quellsprache (Standard). Beim Bauen wird zusätzlich in diese
# Sprachen übersetzt (kostenlose MyMemory-API, kein Key nötig) und ALLES direkt
# mit ins HTML eingebettet — ein Button schaltet clientseitig um, wie beim
# Theme-Toggle. Kein Server, keine Internetverbindung für Besucher nötig.
LANGUAGES = {"en": "English", "es": "Español", "fr": "Français"}
TRANSLATION_CACHE_FILE = Path(__file__).parent / ".translations-cache.json"
# Einzige Quelle für den 3D-Viewer ist die Vorlage im Geschwister-Ordner
# "portfolio vorlagen/3d-viewer/" (liegt außerhalb dieses Repos, nur zum Bauen
# nötig) — von dort kopiert sync_project_3d_viewers() pro Projekt eine eigene,
# in sich geschlossene Kopie nach projekte/<name>/3d-viewer/ (die landet im Repo).
VIEWER_SOURCE = Path(__file__).parent.parent / "portfolio vorlagen" / "3d-viewer"


def parse_kv_block(text: str) -> dict:
    """Parst 'schlüssel: wert'-Zeilen (eine pro Zeile) in ein dict."""
    values = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        values[key.strip().lower()] = value.strip()
    return values


def load_site_content() -> dict:
    """Liest inhalt.txt (Name, Rolle, About, Skills, Kontakt-Links)."""
    if not SITE_CONTENT.exists():
        return {}
    return parse_kv_block(SITE_CONTENT.read_text(encoding="utf-8"))


def parse_checklist_section(text: str, header: str) -> list:
    """Sucht '## {header}' in inhalt.txt und parst die Zeilen darunter als
    Checkliste: '[x] Erledigt' / '[ ] Offen'. Endet an der nächsten '##'-Zeile
    oder am Textende. Gibt [] zurück, wenn der Abschnitt fehlt."""
    m = re.search(rf'^##\s*{re.escape(header)}\s*$', text, re.MULTILINE)
    if not m:
        return []
    rest = text[m.end():]
    next_section = re.search(r'^##(?!#)\s*.+$', rest, re.MULTILINE)
    block = rest[:next_section.start()] if next_section else rest
    steps = []
    for line in block.splitlines():
        line = line.strip()
        cm = re.match(r'^\[([ xX])\]\s*(.+)$', line)
        if cm:
            steps.append({"done": cm.group(1).lower() == "x", "text": cm.group(2).strip()})
    return steps


def parse_timeline_section(text: str) -> list:
    """Sucht '## Zeitstrahl' in inhalt.txt und parst die Zeilen darunter. Der
    Zeitstrahl wird NICHT automatisch aus allen Projekten gebaut — nur Zeilen,
    die hier explizit stehen, erscheinen (in dieser Reihenfolge).

    Format pro Zeile:
    - Für ein echtes Projekt reicht der ORDNERNAME allein (z.B. 'Satellite-Tools').
      Jahr, Kurzbeschreibung und der Link werden dann automatisch aus dessen
      projekt.txt gezogen ('year' bzw. 'timeline', fällt auf 'tagline' zurück) —
      nichts doppelt pflegen.
    - Für einen Meilenstein OHNE eigene Projektseite: 'Freitext-Titel | Jahr'.
    - Optionale Felder je Zeile: 'Titel | Jahr | Projektordner'. Ein hier
      angegebenes Jahr wird nur benutzt, wenn kein Projektordner passt oder
      dessen projekt.txt kein 'year' hat; sonst gewinnt das Jahr aus dem Projekt.
    - Passt weder Feld 3 noch der Titel zu einem Ordner unter projekte/,
      erscheint der Eintrag ohne Link (reiner Meilenstein)."""
    m = re.search(r'^##\s*Zeitstrahl\s*$', text, re.MULTILINE)
    if not m:
        return []
    rest = text[m.end():]
    entries = []
    for line in rest.splitlines():
        line = line.strip()
        if not line or (line.startswith("#") and not line.startswith("##")):
            continue  # Leerzeile oder Kommentar — weiter im Abschnitt.
        if line.startswith("##") or re.match(r'^[A-Za-z_][\w.-]*:\s', line):
            # Nächster '##'-Abschnitt oder wieder eine normale 'schlüssel: wert'-
            # Zeile — der Zeitstrahl-Abschnitt endet hier.
            break
        parts = [p.strip() for p in line.split("|")]
        titel = parts[0] if parts else ""
        if not titel:
            continue
        jahr = parts[1] if len(parts) > 1 else ""
        projektordner = parts[2] if len(parts) > 2 else ""
        if not projektordner:
            # Kein 3. Feld: Titel selbst als Ordnername probieren (Titel ==
            # Ordnername ist der Normalfall).
            projektordner = titel
        href = ""
        beschreibung = ""
        if projektordner and (BASE / projektordner).is_dir():
            href = f"projekte/{projektordner}/index.html"
            project_meta = load_project(BASE / projektordner)
            beschreibung = project_meta.get("timeline") or project_meta.get("tagline", "")
            # Jahr automatisch aus der projekt.txt; nur wenn das Projekt keins
            # angibt, bleibt ein hier eingetragenes Jahr als Rückfall stehen.
            jahr = project_meta.get("year", "") or jahr
        entries.append({"title": titel, "year": jahr, "short_description": beschreibung, "href": href})
    return entries


def fetch_github_repos(username: str, limit: int = 4) -> list:
    """Holt die zuletzt aktualisierten öffentlichen Repos eines GitHub-Users
    über die öffentliche API (kein Key nötig, generöses Rate-Limit für
    gelegentliche Build-Läufe). Bei Netzwerkfehlern/Rate-Limit wird [] zurück-
    gegeben — die Website baut dann einfach ohne diesen Abschnitt weiter."""
    if not username:
        return []
    url = f"https://api.github.com/users/{urllib.parse.quote(username)}/repos?sort=pushed&direction=desc&per_page={limit}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "portfolio-build-script"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        print(f"    ! GitHub-Aktivität konnte nicht geladen werden: {e}")
        return []
    if not isinstance(data, list):
        print(f"    ! GitHub-API-Antwort unerwartet (evtl. Rate-Limit erreicht).")
        return []
    return [
        {
            "name": r.get("name", ""),
            "url": r.get("html_url", ""),
            "description": r.get("description") or "",
            "language": r.get("language") or "",
            "stars": r.get("stargazers_count", 0),
            "updated": (r.get("pushed_at") or "")[:10],
        }
        for r in data[:limit]
    ]


def fetch_leetcode_stats(username: str) -> dict:
    """Holt von der öffentlichen (inoffiziellen) GraphQL-API von leetcode.com
    (kein Key nötig) alles, was der LeetCode-Abschnitt der Startseite anzeigt:
    gelöste Aufgaben je Schwierigkeit, Gesamt-Ranking, Annahmequote, aktuelle
    Serie, aktive Tage, genutzte Sprachen und den Einsende-Kalender (für das
    Aktivitäts-Raster). Bei Netzwerkfehlern oder unerwarteter Antwort wird {}
    zurückgegeben — die Website baut dann einfach ohne diesen Abschnitt weiter.
    Einzelne fehlende Felder sind unkritisch: der Renderer blendet sie aus."""
    if not username:
        return {}
    query = {
        "query": (
            "query userStats($username: String!) {"
            " allQuestionsCount { difficulty count }"
            " matchedUser(username: $username) {"
            "   username"
            "   profile { ranking }"
            "   submitStatsGlobal {"
            "     acSubmissionNum { difficulty count submissions }"
            "     totalSubmissionNum { difficulty count submissions }"
            "   }"
            "   languageProblemCount { languageName problemsSolved }"
            "   userCalendar { streak totalActiveDays submissionCalendar }"
            " } }"
        ),
        "variables": {"username": username},
    }
    try:
        req = urllib.request.Request(
            "https://leetcode.com/graphql",
            data=json.dumps(query).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "portfolio-build-script",
                "Referer": f"https://leetcode.com/{urllib.parse.quote(username)}/",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        print(f"    ! LeetCode-Stats konnten nicht geladen werden: {e}")
        return {}
    payload = data or {}
    matched = payload.get("data", {}).get("matchedUser")
    if not matched:
        print(f"    ! LeetCode-User '{username}' nicht gefunden.")
        return {}
    stats_global = matched.get("submitStatsGlobal") or {}

    def _by_difficulty(entries: list) -> dict:
        out = {"Easy": 0, "Medium": 0, "Hard": 0, "All": 0}
        for entry in entries or []:
            diff = entry.get("difficulty")
            if diff in out:
                out[diff] = entry.get("count", 0)
        return out

    counts = _by_difficulty(stats_global.get("acSubmissionNum"))
    # Gesamtzahl der auf LeetCode existierenden Aufgaben je Schwierigkeit —
    # nur noch als kleiner Kontext ("/ 961") neben der gelösten Zahl.
    totals = _by_difficulty(payload.get("data", {}).get("allQuestionsCount"))

    # Annahmequote = angenommene Einsendungen / alle Einsendungen (wie im
    # LeetCode-Profil). submissions (nicht count) zählt jede Einsendung.
    def _all_submissions(entries: list) -> int:
        for entry in entries or []:
            if entry.get("difficulty") == "All":
                return entry.get("submissions", 0) or 0
        return 0

    accepted_subs = _all_submissions(stats_global.get("acSubmissionNum"))
    total_subs = _all_submissions(stats_global.get("totalSubmissionNum"))
    acceptance = (accepted_subs / total_subs * 100) if total_subs else None

    languages = sorted(
        (
            (entry.get("languageName", ""), entry.get("problemsSolved", 0))
            for entry in (matched.get("languageProblemCount") or [])
            if entry.get("problemsSolved", 0) > 0 and entry.get("languageName")
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )

    cal_raw = matched.get("userCalendar") or {}
    calendar: dict = {}
    try:
        for ts, num in json.loads(cal_raw.get("submissionCalendar") or "{}").items():
            day = datetime.datetime.fromtimestamp(
                int(ts), datetime.timezone.utc
            ).date().isoformat()
            calendar[day] = calendar.get(day, 0) + int(num)
    except (ValueError, TypeError, json.JSONDecodeError):
        calendar = {}

    return {
        "username": matched.get("username", username),
        "ranking": (matched.get("profile") or {}).get("ranking"),
        "easy": counts["Easy"],
        "medium": counts["Medium"],
        "hard": counts["Hard"],
        "total": counts["All"],
        "easy_total": totals["Easy"],
        "medium_total": totals["Medium"],
        "hard_total": totals["Hard"],
        "all_total": totals["All"],
        "acceptance": acceptance,
        "streak": cal_raw.get("streak") or 0,
        "active_days": cal_raw.get("totalActiveDays") or 0,
        "languages": languages,
        "calendar": calendar,
    }


def render_footer_links_html(site: dict) -> str:
    """Kontakt-Links (GitHub/LinkedIn/Email aus inhalt.txt) für den Footer der Projektseiten."""
    parts = []
    if site.get("github"):
        parts.append(f'<li><a href="{html_escape(site["github"])}" target="_blank" rel="noopener noreferrer" data-i18n="github_label" data-i18n-default="GitHub">GitHub</a></li>')
    if site.get("linkedin"):
        parts.append(f'<li><a href="{html_escape(site["linkedin"])}" target="_blank" rel="noopener noreferrer" data-i18n="linkedin_label" data-i18n-default="LinkedIn">LinkedIn</a></li>')
    if site.get("email"):
        parts.append(f'<li><a href="mailto:{html_escape(site["email"])}" data-i18n="email_label" data-i18n-default="Email">Email</a></li>')
    return ''.join(parts)


def html_escape(text: str) -> str:
    """Escaped HTML entities for safe output."""
    return (text
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
        .replace("'", '&apos;'))


def format_content(text: str) -> str:
    """
    Formatiert den Inhalt eines Sections: Listen, Absätze, Code-Blöcke.
    - Listen (- oder *) -> <ul><li>...</li></ul>
    - Code-Blöcke (```) -> <pre><code>...</code></pre>
    - Leerzeilen -> <br>
    - Normale Absätze -> <p class="desc-paragraph">...</p>
    """
    if not text:
        return ''
    lines = text.split('\n')
    parts = []
    in_list = False
    in_code = False
    code_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('```'):
            if in_code:
                parts.append('<pre><code>' + html_escape('\n'.join(code_lines)) + '</code></pre>')
                code_lines = []
                in_code = False
            else:
                if in_list:
                    parts.append('</ul>')
                    in_list = False
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if line.startswith('- ') or line.startswith('* '):
            if not in_list:
                parts.append('<ul>')
                in_list = True
            parts.append(f'<li>{render_text_with_links(line[2:])}</li>')
        else:
            if in_list:
                parts.append('</ul>')
                in_list = False
            if line.strip() == '':
                parts.append('<br>')
            elif line.startswith('```'):
                parts.append(line)
            else:
                parts.append(f'<p class="desc-paragraph">{render_text_with_links(line)}</p>')
    if in_list:
        parts.append('</ul>')
    if in_code:
        parts.append('<pre><code>' + html_escape('\n'.join(code_lines)) + '</code></pre>')
    return ''.join(parts)


def html_escape(text: str) -> str:
    """Escaped HTML entities for safe output."""
    return (text
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
        .replace("'", '&apos;'))


_translation_cache = None
# Wird beim ersten API-Fehschlag (Netzwerk, Rate-Limit/Tageslimit) auf True
# gesetzt: verhindert, dass für JEDEN weiteren Text im selben Build-Lauf
# erneut die API angefragt und dieselbe Fehlermeldung wiederholt wird. Beim
# nächsten "python3 update_website.py" wird automatisch wieder normal
# versucht (der Schalter lebt nur innerhalb eines Laufs).
_translation_disabled = False


def _load_translation_cache() -> dict:
    global _translation_cache
    if _translation_cache is None:
        if TRANSLATION_CACHE_FILE.exists():
            try:
                _translation_cache = json.loads(TRANSLATION_CACHE_FILE.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                _translation_cache = {}
        else:
            _translation_cache = {}
    return _translation_cache


def save_translation_cache() -> None:
    """Schreibt den Übersetzungs-Cache zurück auf die Platte (am Ende von main())."""
    if _translation_cache is not None:
        TRANSLATION_CACHE_FILE.write_text(
            json.dumps(_translation_cache, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def translate_text(text: str, target_lang: str) -> str:
    """Übersetzt eine einzelne Zeile/einen Satz per MyMemory-API (kostenlos,
    kein Key). Ergebnisse werden lokal gecacht — nur neuer/geänderter Text
    verursacht einen echten API-Aufruf. Schlägt die Übersetzung fehl (Netzwerk,
    Rate-Limit/Tageslimit, leerer Text), wird der deutsche Originaltext
    zurückgegeben, damit die Seite nie kaputtgeht — der Fehlschlag wird dabei
    NICHT gecacht, damit beim nächsten Build (z.B. nach Ablauf des
    Tageslimits) automatisch erneut ein echter Übersetzungsversuch passiert,
    statt für immer beim deutschen Text hängen zu bleiben."""
    global _translation_disabled
    text = text.strip()
    if not text:
        return text
    cache = _load_translation_cache()
    key = f"{target_lang}:{text}"
    if key in cache:
        return cache[key]
    if _translation_disabled:
        return text

    try:
        params = urllib.parse.urlencode({"q": text[:490], "langpair": f"de|{target_lang}"})
        url = f"https://api.mymemory.translated.net/get?{params}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        candidate = (data.get("responseData") or {}).get("translatedText", "").strip()
        # MyMemory hängt bei Fuzzy-Treffern manchmal XLIFF-Markup an (<g id="1">…</g>,
        # <bx id="2"/> usw.) — das ist kein echtes HTML, nur Tag-Reste, raus damit.
        candidate = re.sub(r'</?g[^>]*>|<[a-z]{2}\s+id="\d+"\s*/>', '', candidate).strip()
        if candidate and "MYMEMORY WARNING" not in candidate.upper():
            cache[key] = candidate
            return candidate
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        print(f"    ! Übersetzung fehlgeschlagen ({e}) — überspringe alle weiteren Übersetzungen für diesen Build, behalte Deutsch (wird beim nächsten Build erneut versucht).")
        _translation_disabled = True

    return text


def translate_description(text: str, target_lang: str) -> str:
    """Übersetzt einen mehrzeiligen Beschreibungstext zeilenweise, erhält dabei
    ###-Überschriften, Listenpunkte ("- "/"* ") und Codeblöcke (```...```)
    unangetastet in ihrer Struktur — nur der jeweilige Inhalt wird übersetzt."""
    if not text:
        return text
    out = []
    in_code = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            out.append(line)
            continue
        if in_code or not stripped:
            out.append(line)
            continue
        m = re.match(r'^(#{1,3}\s*)(.+)$', line)
        if m:
            out.append(m.group(1) + translate_text(m.group(2), target_lang))
            continue
        if line.startswith("- ") or line.startswith("* "):
            out.append(line[:2] + translate_text(line[2:], target_lang))
            continue
        out.append(translate_text(line, target_lang))
    return "\n".join(out)


LINK_PATTERN = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
BUTTON_PATTERN = re.compile(r'Button:([^:\n]+):(\S+)')


def render_text_with_links(text: str) -> str:
    """Escaped Text, versteht dabei aber zwei Inline-Formen:
    - [Beschriftung](URL) -> normaler Link mitten im Satz.
    - Button:Beschriftung:URL -> richtiger Button mitten im Fließtext (URL darf
      keine Leerzeichen enthalten).
    Beides lässt sich frei zwischen den Text packen statt nur als fester Button
    oben zu erscheinen. Alles außerhalb davon wird ganz normal escaped."""
    matches = sorted(
        list(LINK_PATTERN.finditer(text)) + list(BUTTON_PATTERN.finditer(text)),
        key=lambda m: m.start(),
    )
    parts = []
    last = 0
    for m in matches:
        if m.start() < last:
            continue  # überlappender Treffer -> ignorieren
        parts.append(html_escape(text[last:m.start()]))
        label = html_escape(m.group(1))
        url = html_escape(m.group(2))
        if m.re is BUTTON_PATTERN:
            parts.append(f'<a class="btn btn--ghost btn--sm" href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>')
        else:
            parts.append(f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>')
        last = m.end()
    parts.append(html_escape(text[last:]))
    return ''.join(parts)


def parse_description(text: str) -> str:
    """
    Parses the description text and converts ### headings into structured HTML blocks.
    - ### Heading -> <div class="section-block"><h2 class="section-title">...</h2><div class="section-content">...</div></div>
    - Lists (- or *) -> <ul><li>...</li></ul>
    - Normal paragraphs -> <p class="desc-paragraph">...</p>

    Akzeptiert auch '###Foo' (ohne Leerzeichen) als Heading.
    """
    if not text:
        return ""

    html = ""
    current_section_title = None
    current_content = []

    def flush():
        nonlocal html
        if current_section_title is None and not current_content:
            return
        content_text = '\n'.join(current_content).strip()
        if current_section_title:
            html += '<div class="section-block">'
            html += f'<h2 class="section-title">{html_escape(current_section_title)}</h2>'
            html += f'<div class="section-content">{format_content(content_text)}</div>'
            html += '</div>'
        elif current_content:
            html += f'<div class="section-block"><div class="section-content">{format_content(content_text)}</div></div>'

    for line in text.split('\n'):
        m = re.match(r'^###\s*(.+)$', line)
        if m:
            flush()
            current_section_title = m.group(1).strip()
            current_content = []
        else:
            current_content.append(line)

    flush()
    return html


def parse_project_file(path: Path, folder_name: str = "") -> dict:
    """
    Parst eine projekt.txt: Kopfbereich mit 'schlüssel: wert'-Zeilen,
    danach (durch eine Leerzeile getrennt) die Beschreibung mit
    ###-Überschriften, danach optional weitere ##-Abschnitte:
    - '## Bilder': eine Zeile pro Bild/Video im Format
      'datei | alt-text | bildunterschrift'. Dateien mit einer Video-Endung
      (mp4/webm/mov/m4v/ogv) werden automatisch als abspielbares Video statt
      als Bild gerendert. Liegen im bilder/-Ordner dieses Projekts.
    - '## Dateien': eine Zeile pro Download (z.B. .stl/.zip/.pdf) im Format
      'datei | anzeigename'. Liegen im dateien/-Ordner dieses Projekts.
    - '## Links': eine Zeile pro zusätzlichem Button im Format
      'Beschriftung | URL' — für beliebige weitere Links neben Source/Live/3D-Ansicht.

    Innerhalb der Beschreibung selbst (Absätze/Listenpunkte) wird zusätzlich
    [Beschriftung](URL) als Inline-Link erkannt, lässt sich also frei zwischen den
    Text packen statt nur als Button oben zu erscheinen.

    Der angezeigte Projekt-Titel ist IMMER der Ordnername (folder_name) — er
    wird überall verwendet (Startseite, Übersicht, Projektseite, 3D-Viewer).
    Ein 'title:'-Feld in der projekt.txt gibt es nicht mehr; steht dort eines,
    wird es ignoriert.
    """
    if not path.exists():
        return {
            "title": folder_name,
            "tagline": "", "role": "", "year": "", "stack": [],
            "links": {"repo": "", "live": "", "3d-viewer": ""}, "featured": False,
            "timeline": "", "description": "", "images": [], "files": [], "custom_links": [],
        }

    text = path.read_text(encoding="utf-8")
    header_block, _, rest = text.partition("\n\n")
    kv = parse_kv_block(header_block)

    # (?!#) sorgt dafür, dass ###-Überschriften (Teil der Beschreibung) NICHT
    # mit "##"-Abschnitten (Bilder/Dateien/Links) verwechselt werden.
    section_pattern = re.compile(r'^##(?!#)\s*(.+?)\s*$', re.MULTILINE)
    section_matches = list(section_pattern.finditer(rest))
    if section_matches:
        description = rest[:section_matches[0].start()].strip("\n")
    else:
        description = rest.strip("\n")

    sections = {}
    for i, m in enumerate(section_matches):
        name = m.group(1).strip().lower()
        start = m.end()
        end = section_matches[i + 1].start() if i + 1 < len(section_matches) else len(rest)
        sections[name] = rest[start:end].strip("\n")

    images = []
    for line in sections.get("bilder", "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        file = parts[0] if len(parts) > 0 else ""
        if not file:
            continue
        images.append({
            "file": file,
            "alt": parts[1] if len(parts) > 1 else "",
            "caption": parts[2] if len(parts) > 2 else "",
        })

    files = []
    for line in sections.get("dateien", "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        file = parts[0] if len(parts) > 0 else ""
        if not file:
            continue
        files.append({
            "file": file,
            "label": parts[1] if len(parts) > 1 else file,
        })

    custom_links = []
    for line in sections.get("links", "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        label = parts[0] if len(parts) > 0 else ""
        url = parts[1] if len(parts) > 1 else ""
        if not label or not url:
            continue
        width = parts[2] if len(parts) > 2 and parts[2].strip().isdigit() else ""
        height = parts[3] if len(parts) > 3 and parts[3].strip().isdigit() else ""
        custom_links.append({"label": label, "url": url, "width": width, "height": height})

    stack = [s.strip() for s in kv.get("stack", "").split(",") if s.strip()]
    featured = kv.get("featured", "").strip().lower() in ("true", "1", "yes", "ja")

    return {
        "title": folder_name,
        "tagline": kv.get("tagline", ""),
        "role": kv.get("role", ""),
        "year": kv.get("year", ""),
        "custom_links": custom_links,
        "stack": stack,
        "links": {
            "repo": kv.get("repo", ""),
            "live": kv.get("live", ""),
            "3d-viewer": kv.get("3d-viewer", ""),
        },
        "featured": featured,
        "timeline": kv.get("timeline", ""),
        "description": description,
        "images": images,
        "files": files,
    }


def load_project(project_dir: Path) -> dict:
    """Liest projekt.txt aus dem Projektordner (die einzige Textdatei mit
    allen Texten, Überschriften, Links und Bildern dieses Projekts). Der
    angezeigte Projekt-Titel ist immer der Ordnername."""
    return parse_project_file(project_dir / PROJECT_FILE_NAME, folder_name=project_dir.name)


VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "m4v", "ogv"}


def render_gallery(images: list) -> str:
    """Rendert die Bilder-/Video-Galerie als <li>-Liste (im CSS drei pro Reihe,
    danach Zeilenumbruch).

    Einträge mit einer Video-Dateiendung (mp4/webm/mov/m4v/ogv) werden als
    abspielbares <video controls> gerendert, alle anderen als <img>. Die
    Bildunterschrift (das 3. Feld je Zeile im ## Bilder-Abschnitt) erscheint —
    sofern gesetzt — unter dem Bild bzw. Video."""
    if not images:
        return ""
    items = []
    for img in images:
        raw_file = img.get("file", "")
        file = html_escape(raw_file)
        alt = html_escape(img.get("alt", ""))
        caption = html_escape(img.get("caption", ""))
        ext = raw_file.rsplit(".", 1)[-1].lower() if "." in raw_file else ""

        if ext in VIDEO_EXTENSIONS:
            media = (
                f'<video class="gallery__image" src="bilder/{file}" '
                f'controls muted playsinline preload="metadata" aria-label="{alt}"></video>'
            )
        else:
            media = f'<img class="gallery__image" src="bilder/{file}" alt="{alt}" loading="lazy">'

        caption_html = f'<div class="gallery__caption">{caption}</div>' if caption else ''
        items.append(
            f'<li class="gallery__item">'
            f'<div class="gallery__media">{media}</div>'
            f'{caption_html}'
            f'</li>'
        )
    return ''.join(items)


def render_files_list(files: list) -> str:
    """Rendert die Download-Liste (## Dateien) als <li>-Liste im dateien/-Ordner."""
    if not files:
        return ""
    items = []
    for f in files:
        file = html_escape(f.get("file", ""))
        label = html_escape(f.get("label") or f.get("file", ""))
        items.append(
            f'<li><a href="dateien/{file}" download>'
            f'<span class="files__name">{label}</span>'
            f'<span class="files__icon" aria-hidden="true">↓</span>'
            f'</a></li>'
        )
    return ''.join(items)


def render_meta_html(meta: dict) -> str:
    """Header-Block (eyebrow, h1, tagline, meta-Span, Skills)."""
    title = html_escape(meta.get("title", ""))
    tagline = html_escape(meta.get("tagline", ""))
    role = html_escape(meta.get("role", ""))
    year = html_escape(str(meta.get("year", "")))
    stack = meta.get("stack", []) or []
    stack_html = " ".join(f"<span>· {html_escape(s)}</span>" for s in stack)
    skills_html = "".join(f"<li>{html_escape(s)}</li>" for s in stack)
    eyebrow = f"{role} · {year}".strip(" ·") if role or year else "Projekt"

    return (
        f'<span class="eyebrow reveal">{eyebrow}</span>'
        f'<h1 class="reveal">{title}</h1>'
        f'<p class="hero__title reveal">{tagline}</p>'
        f'<div class="detail__meta reveal">{stack_html}</div>'
        f'<ul class="skills" aria-label="Tech Stack" style="margin-top: 16px;">{skills_html}</ul>'
    )


def render_links_html(meta: dict, project_dir: Path | None = None) -> str:
    """Detail-Links (Demo / Source / Live / 3D-Ansicht) als Button-Liste."""
    links = meta.get("links", {}) or {}
    parts = []
    if project_dir is not None and (project_dir / "details.html").exists():
        parts.append(
            '<a class="btn btn--primary" href="details.html" data-i18n="demo_button" '
            'data-i18n-default="Demo ansehen">Demo ansehen</a>'
        )
    if links.get("repo"):
        parts.append(
            f'<a class="btn btn--ghost" href="{html_escape(links["repo"])}" '
            f'target="_blank" rel="noopener noreferrer" data-i18n="source_button" '
            f'data-i18n-default="Source">Source</a>'
        )
    if links.get("live"):
        parts.append(
            f'<a class="btn btn--primary" href="{html_escape(links["live"])}" '
            f'target="_blank" rel="noopener noreferrer" data-i18n="live_button" '
            f'data-i18n-default="Live">Live</a>'
        )
    if links.get("3d-viewer"):
        parts.append(
            f'<a class="btn btn--primary" href="{html_escape(links["3d-viewer"])}" '
            f'target="_blank" rel="noopener noreferrer" data-i18n="3d_button" '
            f'data-i18n-default="3D-Ansicht">3D-Ansicht</a>'
        )
    for link in meta.get("custom_links", []) or []:
        style = ""
        if link.get("width") or link.get("height"):
            w = f'width:{link["width"]}px;' if link.get("width") else ""
            h = f'height:{link["height"]}px;' if link.get("height") else ""
            style = f' style="{w}{h}"'
        parts.append(
            f'<a class="btn btn--ghost" href="{html_escape(link["url"])}"{style} '
            f'target="_blank" rel="noopener noreferrer">{html_escape(link["label"])}</a>'
        )
    return ''.join(parts)


def build_project_index(project_dir: Path) -> str:
    """Baut die komplette index.html für ein Projekt."""
    if not project_dir.name:
        return ""

    meta = load_project(project_dir)
    raw_description = meta.get("description", "")
    raw_tagline = meta.get("tagline", "")
    body_html = parse_description(raw_description)

    # Übersetzte Beschreibung + Tagline: je Sprache ein eigener, per JS
    # umschaltbarer Block (siehe LANG_SWITCH_SCRIPT). Deutsch ist das Original
    # und bleibt unverändert; en/es/fr kommen aus translate_description()/
    # translate_text() (gecacht, kein erneuter API-Aufruf bei unverändertem Text).
    body_i18n_html = f'<div data-i18n-lang="de">{body_html}</div>'
    tagline_i18n_html = f'<p class="hero__title reveal" data-i18n-lang-el="de">{html_escape(raw_tagline)}</p>'
    for lang in LANGUAGES:
        translated_body = parse_description(translate_description(raw_description, lang))
        body_i18n_html += f'<div data-i18n-lang="{lang}">{translated_body}</div>'
        translated_tagline = html_escape(translate_text(raw_tagline, lang))
        tagline_i18n_html += f'<p class="hero__title reveal" data-i18n-lang-el="{lang}">{translated_tagline}</p>'

    # Nur Bilder anzeigen, deren Datei auch wirklich im bilder/-Ordner liegt —
    # sonst blieben leere Slots mit Bildunterschrift für fehlende Dateien stehen.
    images = [
        img for img in meta.get("images", [])
        if (project_dir / "bilder" / img["file"]).is_file()
    ]
    gallery_html = render_gallery(images)
    footer_links_html = render_footer_links_html(load_site_content())
    gallery_section = ""
    if images:
        gallery_section = (
            '\n      <section class="section--tight container reveal" style="margin-top: 32px;">\n'
            '        <span class="eyebrow" data-i18n="insights_eyebrow" data-i18n-default="Einblicke">Einblicke</span>\n'
            '        <h2 class="section__title" data-i18n="images_heading" data-i18n-default="Bilder">Bilder</h2>\n'
            f'        <ul class="gallery" id="gallery">{gallery_html}</ul>\n'
            '      </section>'
        )

    # Nur Downloads anzeigen, deren Datei auch wirklich im dateien/-Ordner liegt.
    downloadable_files = [
        f for f in meta.get("files", [])
        if (project_dir / "dateien" / f["file"]).is_file()
    ]
    files_section = ""
    if downloadable_files:
        files_section = (
            '\n      <section class="section--tight container reveal" style="margin-top: 32px;">\n'
            '        <span class="eyebrow" data-i18n="downloads_eyebrow" data-i18n-default="Downloads">Downloads</span>\n'
            '        <h2 class="section__title" data-i18n="files_heading" data-i18n-default="Dateien">Dateien</h2>\n'
            f'        <ul class="files">{render_files_list(downloadable_files)}</ul>\n'
            '      </section>'
        )

    links_html = render_links_html(meta, project_dir)

    eyebrow = html_escape(f"{meta.get('role', '')} · {meta.get('year', '')}".strip(" ·"))
    title = html_escape(meta["title"])  # immer der Ordnername
    tagline = html_escape(raw_tagline)
    skills_html = "".join(f"<li>{html_escape(s)}</li>" for s in meta.get("stack", []))
    lang_switch = lang_switch_html()
    i18n_script_data = i18n_json([
        "nav_home", "brand_portfolio", "back_to_portfolio", "demo_button",
        "source_button", "live_button", "3d_button", "insights_eyebrow",
        "images_heading", "downloads_eyebrow", "files_heading", "footer_text",
        "github_label", "linkedin_label", "email_label", "translation_note",
    ])

    template = """<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title} — {tagline}</title>
  <link rel="stylesheet" href="../../style.css" />
</head>
<body>
  <header class="site-header">
    <div class="container site-header__inner">
      <a href="../../index.html" class="brand" data-home data-i18n="brand_portfolio" data-i18n-default="Portfolio">Portfolio</a>
      <nav class="nav" aria-label="Hauptnavigation">
        <a class="nav__link" href="../../index.html" data-home data-i18n="nav_home" data-i18n-default="Home">Home</a>
        <button class="theme-toggle" data-theme-toggle aria-label="Theme wechseln">◐</button>
        {lang_switch}
      </nav>
    </div>
  </header>

  <main id="main">
    <article class="section container detail">
      <a class="detail__back" href="../../index.html" data-home data-i18n="back_to_portfolio" data-i18n-default="← Zurück zum Portfolio">← Zurück zum Portfolio</a>
      <header class="detail__header">
        <span class="eyebrow reveal">{eyebrow}</span>
        <h1 class="reveal">{title}</h1>
        {tagline_i18n_html}
        <ul class="skills" aria-label="Tech Stack" style="margin-top: 16px;">{skills_html}</ul>
        <div class="detail__links reveal">{links_html}</div>
      </header>
      <div class="detail__body reveal" id="description">{body_i18n_html}</div>{files_section}{gallery_section}
    </article>
  </main>

  <footer class="site-footer">
    <div class="container footer__inner">
      <div class="footer__text-group">
        <span data-i18n="footer_text" data-i18n-default="© 2026 — gebaut mit Vite, HTML &amp; CSS.">© 2026 — gebaut mit Vite, HTML &amp; CSS.</span>
        {translation_note_html}
      </div>
      <ul class="footer__links">{footer_links_html}</ul>
    </div>
  </footer>
  <script>const I18N = {i18n_script_data};</script>
  <script>
    (function() {{
      const KEY = 'theme';
      const root = document.documentElement;
      const toggle = document.querySelector('[data-theme-toggle]');
      function apply(theme) {{
        root.setAttribute('data-theme', theme);
        localStorage.setItem(KEY, theme);
      }}
      function init() {{
        const saved = localStorage.getItem(KEY);
        const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        apply(saved || (prefersDark ? 'dark' : 'light'));
      }}
      toggle?.addEventListener('click', () => {{
        const current = root.getAttribute('data-theme');
        apply(current === 'dark' ? 'light' : 'dark');
      }});
      init();
      {lang_switch_script}

      // Reveal-on-scroll: ohne dieses Observer-Script bleiben .reveal-Elemente
      // dauerhaft unsichtbar (opacity: 0 aus style.css).
      const observer = new IntersectionObserver((entries) => {{
        entries.forEach((entry) => {{
          if (entry.isIntersecting) {{
            entry.target.classList.add('is-visible');
            observer.unobserve(entry.target);
          }}
        }});
      }}, {{ threshold: 0.08, rootMargin: '0px 0px -10% 0px' }});
      document.querySelectorAll('.reveal').forEach((el) => observer.observe(el));

      // file:// Fallback: ohne IntersectionObserver-Support alles sofort zeigen.
      if (location.protocol === 'file:' || !('IntersectionObserver' in window)) {{
        document.querySelectorAll('.reveal').forEach((el) => el.classList.add('is-visible'));
      }}
    }})();
  </script>
</body>
</html>"""

    page = template.format(
        title=title,
        tagline=tagline,
        eyebrow=eyebrow,
        skills_html=skills_html,
        tagline_i18n_html=tagline_i18n_html,
        body_i18n_html=body_i18n_html,
        files_section=files_section,
        gallery_section=gallery_section,
        links_html=links_html,
        footer_links_html=footer_links_html,
        translation_note_html=TRANSLATION_NOTE_HTML,
        lang_switch=lang_switch,
        lang_switch_script=LANG_SWITCH_SCRIPT,
        i18n_script_data=i18n_script_data,
    )

    return page


def check_description_structure(project_name: str, description: str) -> None:
    """Weiche Prüfung (bricht den Build NICHT ab): Projektbeschreibungen sollen
    der Standardstruktur folgen — ### Ziel, ### Ablauf, ### Ergebnis, plus
    optional ein frei benannter vierter Punkt. Fehlt eine der drei Pflicht-
    Überschriften, gibt's nur eine Warnung in der Konsole."""
    headings = [h.strip().lower() for h in re.findall(r'^###\s*(.+?)\s*$', description, re.MULTILINE)]
    required = ["ziel", "ablauf", "ergebnis"]
    missing = [r for r in required if r not in headings]
    if missing:
        print(f"  ! {project_name}: Beschreibung folgt nicht der Ziel/Ablauf/Ergebnis-Struktur (fehlt: {', '.join(missing)})")


def update_project(project_dir: Path) -> None:
    """Schreibt die index.html eines Projektordners neu."""
    meta = load_project(project_dir)
    check_description_structure(project_dir.name, meta.get("description", ""))
    html = build_project_index(project_dir)
    (project_dir / "index.html").write_text(html, encoding="utf-8")
    print(f"  ✓ {project_dir.name}/index.html")


UI_STRINGS = {
    "nav_home": {"en": "Home", "es": "Inicio", "fr": "Accueil"},
    "brand_portfolio": {"en": "Portfolio", "es": "Portafolio", "fr": "Portfolio"},
    "back_to_portfolio": {"en": "← Back to portfolio", "es": "← Volver al portafolio", "fr": "← Retour au portfolio"},
    "back_to_project": {"en": "Back to project", "es": "Volver al proyecto", "fr": "Retour au projet"},
    "demo_button": {"en": "View demo", "es": "Ver demo", "fr": "Voir la démo"},
    "source_button": {"en": "Source", "es": "Código", "fr": "Code source"},
    "live_button": {"en": "Live", "es": "En vivo", "fr": "En direct"},
    "3d_button": {"en": "3D view", "es": "Vista 3D", "fr": "Vue 3D"},
    "insights_eyebrow": {"en": "Insights", "es": "Vistazo", "fr": "Aperçu"},
    "images_heading": {"en": "Images", "es": "Imágenes", "fr": "Images"},
    "downloads_eyebrow": {"en": "Downloads", "es": "Descargas", "fr": "Téléchargements"},
    "files_heading": {"en": "Files", "es": "Archivos", "fr": "Fichiers"},
    "footer_text": {
        "en": "© 2026 — built with HTML & CSS.",
        "es": "© 2026 — construido con HTML y CSS.",
        "fr": "© 2026 — conçu avec HTML et CSS.",
    },
    # Hinweis in der Fußzeile, NUR auf übersetzten (nicht-deutschen) Sprachen
    # sichtbar — auf Deutsch bleibt data-i18n-default leer, siehe
    # TRANSLATION_NOTE_HTML unten.
    "translation_note": {
        "en": "This page was translated automatically using AI.",
        "es": "Esta página fue traducida automáticamente con IA.",
        "fr": "Cette page a été traduite automatiquement par IA.",
    },
    "all_work_eyebrow": {"en": "All work", "es": "Todos los trabajos", "fr": "Tous les travaux"},
    "all_projects_heading": {"en": "All projects", "es": "Todos los proyectos", "fr": "Tous les projets"},
    "timeline_nav": {"en": "Timeline", "es": "Cronología", "fr": "Chronologie"},
    "github_label": {"en": "GitHub", "es": "GitHub", "fr": "GitHub"},
    "linkedin_label": {"en": "LinkedIn", "es": "LinkedIn", "fr": "LinkedIn"},
    "email_label": {"en": "Email", "es": "Correo", "fr": "E-mail"},
    "contact_button": {"en": "Contact", "es": "Contacto", "fr": "Contact"},
    "about_eyebrow": {"en": "About", "es": "Sobre mí", "fr": "À propos"},
    "stack_eyebrow": {"en": "Stack", "es": "Tecnologías", "fr": "Technologies"},
    "verlauf_eyebrow": {"en": "History", "es": "Historial", "fr": "Historique"},
    "kontakt_eyebrow": {"en": "Contact", "es": "Contacto", "fr": "Contact"},
    "project_tag": {"en": "Project", "es": "Proyecto", "fr": "Projet"},
    "search_placeholder": {"en": "Search projects…", "es": "Buscar proyectos…", "fr": "Rechercher des projets…"},
    "search_empty": {"en": "No projects found.", "es": "No se encontraron proyectos.", "fr": "Aucun projet trouvé."},
    "current_project_eyebrow": {"en": "Currently building", "es": "En desarrollo", "fr": "En cours de développement"},
    "current_project_link": {"en": "View on GitHub", "es": "Ver en GitHub", "fr": "Voir sur GitHub"},
    "github_activity_eyebrow": {"en": "Live", "es": "En vivo", "fr": "En direct"},
    "github_activity_heading": {"en": "Recently on GitHub", "es": "Recientemente en GitHub", "fr": "Récemment sur GitHub"},
    "github_activity_updated": {"en": "updated", "es": "actualizado", "fr": "mis à jour"},
    "leetcode_eyebrow": {"en": "Live", "es": "En vivo", "fr": "En direct"},
    "leetcode_heading": {"en": "LeetCode", "es": "LeetCode", "fr": "LeetCode"},
    "leetcode_solved": {"en": "solved", "es": "resueltos", "fr": "résolus"},
    "leetcode_ranking": {"en": "Ranking", "es": "Clasificación", "fr": "Classement"},
    "leetcode_easy": {"en": "Easy", "es": "Fácil", "fr": "Facile"},
    "leetcode_medium": {"en": "Medium", "es": "Medio", "fr": "Moyen"},
    "leetcode_hard": {"en": "Hard", "es": "Difícil", "fr": "Difficile"},
    "leetcode_acceptance": {"en": "Acceptance", "es": "Aceptación", "fr": "Taux d’acceptation"},
    "leetcode_streak": {"en": "Day streak", "es": "Días seguidos", "fr": "Jours d’affilée"},
    "leetcode_active_days": {"en": "Active days", "es": "Días activos", "fr": "Jours actifs"},
    "leetcode_languages": {"en": "Languages", "es": "Lenguajes", "fr": "Langages"},
    "leetcode_activity": {"en": "Activity", "es": "Actividad", "fr": "Activité"},
    "leetcode_cal_less": {"en": "less", "es": "menos", "fr": "moins"},
    "leetcode_cal_more": {"en": "more", "es": "más", "fr": "plus"},
}

# Fußzeilen-Hinweis "automatisch übersetzt", der NUR auf den übersetzten
# (nicht-deutschen) Sprachversionen sichtbar wird — data-i18n-default bleibt
# leer, sodass auf Deutsch (und vor dem ersten JS-Sprachwechsel) nichts
# angezeigt wird. Wird in jede Fußzeile eingesetzt (Projektseiten, index.html,
# alle-projekte.html, details.html).
TRANSLATION_NOTE_HTML = (
    '<span class="footer__translation-note" data-i18n="translation_note" '
    'data-i18n-default=""></span>'
)


def lang_switch_html() -> str:
    """Die DE/EN/ES/FR-Buttons für den Header, direkt neben dem Theme-Toggle."""
    buttons = ''.join(
        f'<button type="button" data-lang="{code}" aria-current="{"true" if code == "de" else "false"}">{code.upper()}</button>'
        for code in ("de", *LANGUAGES.keys())
    )
    return f'<div class="lang-switch" role="group" aria-label="Sprache / Language">{buttons}</div>'


def i18n_json(keys: list) -> str:
    """JSON-Objekt {en:{...}, es:{...}, fr:{...}} für die angegebenen UI_STRINGS-
    Schlüssel — wird im generierten <script> als `const I18N = ...;` eingebettet."""
    result = {lang: {k: UI_STRINGS[k][lang] for k in keys if k in UI_STRINGS} for lang in LANGUAGES}
    return json.dumps(result, ensure_ascii=False)


def i18n_span_variants(raw_text: str) -> str:
    """<span data-i18n-lang-el="xx">...</span> je Sprache — zum Einsetzen IN ein
    bestehendes Element hinein (z.B. per Marker in ein <h2>...</h2>)."""
    html = f'<span data-i18n-lang-el="de">{html_escape(raw_text)}</span>'
    for lang in LANGUAGES:
        html += f'<span data-i18n-lang-el="{lang}">{html_escape(translate_text(raw_text, lang))}</span>'
    return html


def render_i18n_block(build_fn) -> str:
    """build_fn(lang) -> HTML-Fragment für diese Sprache ('de' zuerst, dann
    'en'/'es'/'fr'). Ergebnis sind vier data-i18n-lang-Wrapper (display:contents),
    von denen der Sprachumschalter per JS immer nur einen sichtbar macht."""
    html = f'<div data-i18n-lang="de">{build_fn("de")}</div>'
    for lang in LANGUAGES:
        html += f'<div data-i18n-lang="{lang}">{build_fn(lang)}</div>'
    return html


def render_current_project_section(kv: dict) -> str:
    """Baut den 'Aktuelles Projekt'-Abschnitt aus aktuelles_projekt_titel/
    _text/_link + dem Checklisten-Abschnitt '## Aktuelles Projekt Schritte' in
    inhalt.txt. Fortschritt wird automatisch aus den abgehakten Schritten
    berechnet. Gibt "" zurück (Abschnitt entfällt komplett), wenn kein Titel
    gesetzt ist — das Feature ist also rein optional."""
    titel_raw = kv.get("aktuelles_projekt_titel", "")
    if not titel_raw:
        return ""
    text_raw = kv.get("aktuelles_projekt_text", "")
    link = kv.get("aktuelles_projekt_link", "")
    steps = parse_checklist_section(
        SITE_CONTENT.read_text(encoding="utf-8") if SITE_CONTENT.exists() else "",
        "Aktuelles Projekt Schritte",
    )
    done_count = sum(1 for s in steps if s["done"])
    progress = round(done_count / len(steps) * 100) if steps else int(kv.get("aktuelles_projekt_fortschritt", "0") or 0)

    def build(lang):
        titel = html_escape(titel_raw if lang == "de" else translate_text(titel_raw, lang))
        text = html_escape(text_raw if lang == "de" else translate_text(text_raw, lang))
        link_label = "Auf GitHub ansehen" if lang == "de" else UI_STRINGS["current_project_link"][lang]
        steps_html = "".join(
            f'<li class="current-project__step{" is-done" if s["done"] else ""}">'
            f'<span class="current-project__step-check">{"✓" if s["done"] else ""}</span>'
            f'<span class="current-project__step-text">{html_escape(s["text"] if lang == "de" else translate_text(s["text"], lang))}</span>'
            f'</li>'
            for s in steps
        )
        link_html = (
            f'<a class="btn btn--ghost" href="{html_escape(link)}" target="_blank" rel="noopener noreferrer">{link_label} ↗</a>'
            if link else ""
        )
        return (
            f'<div class="current-project__head">'
            f'<h3 style="margin:0;">{titel}</h3>{link_html}'
            f'</div>'
            f'<p class="muted" style="margin-top:8px;">{text}</p>'
            + (f'<ul class="current-project__steps">{steps_html}</ul>' if steps else '')
            + (
                f'<div class="progress-bar"><div class="progress-bar__fill" style="width:{progress}%"></div></div>'
                f'<div class="progress-bar__label"><span>{done_count}/{len(steps)}</span><span>{progress}%</span></div>'
                if steps else ''
            )
        )

    inner = render_i18n_block(build)
    return (
        '\n      <section class="section section--tight container reveal" id="current-project">\n'
        '        <span class="eyebrow" data-i18n="current_project_eyebrow" data-i18n-default="Woran ich gerade arbeite">Woran ich gerade arbeite</span>\n'
        f'        <div class="current-project">{inner}</div>\n'
        '      </section>\n      '
    )


def render_skill_radar_svg(skills: list) -> str:
    """Baut ein Vieleck-/Radar-Diagramm als reines Inline-SVG: eine Ecke pro
    Skill, der Datenpunkt liegt umso weiter außen, je höher das Level (0-100).
    Keine Bibliothek nötig, Farben laufen komplett über CSS-Klassen (also
    hell/dunkel-fähig). Bei weniger als 3 Skills (kein sinnvolles Vieleck)
    fällt die Funktion auf eine einfache Liste zurück."""
    n = len(skills)
    if n == 0:
        return ""
    if n < 3:
        items = "".join(
            f'<li>{html_escape(s["name"])} <span class="skill-radar-fallback__pct">{s["level"]}%</span></li>'
            for s in skills
        )
        return f'<ul class="skill-radar-fallback">{items}</ul>'

    size = 340
    cx = cy = size / 2
    radius = size / 2 - 76  # Rand für die Labels lassen

    def point_at(r, i):
        angle = -math.pi / 2 + i * (2 * math.pi / n)
        return (cx + r * math.cos(angle), cy + r * math.sin(angle))

    def polygon_points(r):
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in (point_at(r, i) for i in range(n)))

    rings = "".join(
        f'<polygon points="{polygon_points(radius * pct / 100)}" class="skill-radar__ring" />'
        for pct in (25, 50, 75, 100)
    )
    axes = "".join(
        f'<line x1="{cx}" y1="{cy}" x2="{point_at(radius, i)[0]:.1f}" y2="{point_at(radius, i)[1]:.1f}" class="skill-radar__axis" />'
        for i in range(n)
    )
    labels = []
    for i, s in enumerate(skills):
        lx, ly = point_at(radius + 30, i)
        anchor = "middle"
        if lx < cx - 4:
            anchor = "end"
        elif lx > cx + 4:
            anchor = "start"
        labels.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" dominant-baseline="middle" '
            f'class="skill-radar__label">{html_escape(s["name"])}</text>'
        )

    data_points = [point_at(radius * s["level"] / 100, i) for i, s in enumerate(skills)]
    data_polygon = " ".join(f"{x:.1f},{y:.1f}" for x, y in data_points)
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" class="skill-radar__dot"><title>{html_escape(skills[i]["name"])}: {skills[i]["level"]}%</title></circle>'
        for i, (x, y) in enumerate(data_points)
    )

    return (
        f'<svg viewBox="0 0 {size} {size}" class="skill-radar__svg" role="img" aria-label="Skill-Diagramm">'
        f'{rings}{axes}'
        f'<polygon points="{data_polygon}" class="skill-radar__data" />'
        f'{dots}{"".join(labels)}'
        f'</svg>'
    )


def render_github_activity_section(repos: list) -> str:
    """Baut den 'Zuletzt auf GitHub'-Abschnitt aus den beim Bauen abgerufenen
    Repos. Gibt "" zurück (Abschnitt entfällt), wenn keine Repos geladen
    werden konnten (kein github: in inhalt.txt, Netzwerkfehler, Rate-Limit)."""
    if not repos:
        return ""
    items = []
    for r in repos:
        meta_parts = []
        if r["language"]:
            meta_parts.append(html_escape(r["language"]))
        if r["stars"]:
            meta_parts.append(f'★ {r["stars"]}')
        if r["updated"]:
            meta_parts.append(f'<span data-i18n="github_activity_updated" data-i18n-default="aktualisiert">aktualisiert</span> {html_escape(r["updated"])}')
        items.append(
            f'<a class="github-activity__item" href="{html_escape(r["url"])}" target="_blank" rel="noopener noreferrer">'
            f'<div class="github-activity__name">📦 {html_escape(r["name"])}</div>'
            + (f'<div class="github-activity__desc">{html_escape(r["description"])}</div>' if r["description"] else '')
            + f'<div class="github-activity__meta">{" · ".join(meta_parts)}</div>'
            f'</a>'
        )
    return (
        '\n      <section class="section section--tight container reveal" id="github-activity">\n'
        '        <span class="eyebrow" data-i18n="github_activity_eyebrow" data-i18n-default="Live">Live</span>\n'
        '        <h2 class="section__title" data-i18n="github_activity_heading" data-i18n-default="Zuletzt auf GitHub">Zuletzt auf GitHub</h2>\n'
        f'        <div class="github-activity">{"".join(items)}</div>\n'
        '      </section>\n      '
    )


def _leetcode_calendar_cells(calendar: dict, weeks: int = 26) -> str:
    """GitHub-artiges Beitrags-Raster: `weeks` Spalten (je Woche, Montag oben,
    Sonntag unten) aus {ISO-Datum: Anzahl}. Fünf Intensitätsstufen über
    data-l (0-4), relativ zum aktivsten Tag im sichtbaren Fenster skaliert.
    Tage in der Zukunft (bis Wochenende) bleiben leer, damit das Raster ein
    sauberes Rechteck ergibt."""
    today = datetime.date.today()
    end = today + datetime.timedelta(days=6 - today.weekday())        # Sonntag dieser Woche
    start = end - datetime.timedelta(days=7 * weeks - 1)              # Montag, `weeks` Wochen früher
    days = [start + datetime.timedelta(days=i) for i in range((end - start).days + 1)]
    peak = max((calendar.get(d.isoformat(), 0) for d in days), default=0)

    def level(count: int) -> int:
        if count <= 0:
            return 0
        if peak <= 1:
            return 2
        return min(4, 1 + int(count / peak * 3.999))

    cells = []
    for d in days:
        iso = d.isoformat()
        count = calendar.get(iso, 0)
        future = " leetcode-cal__cell--future" if d > today else ""
        cells.append(
            f'<i class="leetcode-cal__cell{future}" data-l="{level(count)}" title="{iso}: {count}"></i>'
        )
    return "".join(cells)


def render_leetcode_section(stats: dict) -> str:
    """Baut den LeetCode-Abschnitt der Startseite: eine Karte mit gelöster
    Gesamtzahl + Ranking, einer segmentierten Verteilungs-Leiste (Anteil
    Easy/Medium/Hard an den gelösten Aufgaben — immer gefüllt, nie ein leerer
    Ring), einer Aufschlüsselung je Schwierigkeit, Sekundär-Kennzahlen
    (Annahmequote, Serie, aktive Tage, Sprachen), Sprach-Chips und einem
    Aktivitäts-Raster aus dem Einsende-Kalender. Gibt "" zurück (Abschnitt
    entfällt), wenn keine Stats geladen werden konnten (kein leetcode: in
    inhalt.txt, Netzwerkfehler, User nicht gefunden)."""
    if not stats:
        return ""
    total = stats["total"]
    diffs = (
        ("easy", "leetcode_easy", "Easy", stats["easy"], stats["easy_total"]),
        ("medium", "leetcode_medium", "Medium", stats["medium"], stats["medium_total"]),
        ("hard", "leetcode_hard", "Hard", stats["hard"], stats["hard_total"]),
    )

    # Verteilungs-Leiste: jedes Segment wächst mit der Zahl gelöster Aufgaben
    # dieser Schwierigkeit. Wer nur Easy gelöst hat, bekommt eine voll grüne
    # Leiste — bewusst gefüllt statt zweier leerer Kreise.
    segs = "".join(
        f'<span class="leetcode-split__seg leetcode-split__seg--{key}" '
        f'style="flex-grow:{solved}" title="{label}: {solved}"></span>'
        for key, _i18n, label, solved, _tot in diffs
        if solved > 0
    ) or '<span class="leetcode-split__seg leetcode-split__seg--empty" style="flex-grow:1"></span>'

    diff_rows = "".join(
        '<div class="leetcode-diff">'
        '<div class="leetcode-diff__head">'
        f'<span class="leetcode-diff__dot leetcode-diff__dot--{key}"></span>'
        f'<span class="leetcode-diff__name" data-i18n="{i18n_key}" data-i18n-default="{label}">{label}</span>'
        '</div>'
        '<div class="leetcode-diff__body">'
        f'<span class="leetcode-diff__count">{solved}</span>'
        f'<span class="leetcode-diff__total">/ {tot:,}</span>'
        '</div>'
        '</div>'
        for key, i18n_key, label, solved, tot in diffs
    )

    stat_items = []
    if stats.get("acceptance") is not None:
        stat_items.append((f'{stats["acceptance"]:.0f}%', "leetcode_acceptance", "Annahmequote"))
    stat_items.append((str(stats.get("streak", 0)), "leetcode_streak", "Tage in Serie"))
    stat_items.append((str(stats.get("active_days", 0)), "leetcode_active_days", "Aktive Tage"))
    languages = stats.get("languages") or []
    if languages:
        stat_items.append((str(len(languages)), "leetcode_languages", "Sprachen"))
    stats_html = "".join(
        '<div class="leetcode-stat">'
        f'<span class="leetcode-stat__num">{num}</span>'
        f'<span class="leetcode-stat__label" data-i18n="{i18n_key}" data-i18n-default="{label}">{label}</span>'
        '</div>'
        for num, i18n_key, label in stat_items
    )

    lang_chips = "".join(
        f'<span class="leetcode-lang">{html_escape(name)}<b>{count}</b></span>'
        for name, count in languages[:5]
    )
    lang_html = f'<div class="leetcode-langs">{lang_chips}</div>' if lang_chips else ""
    group_html = (
        '\n          <div class="leetcode-group">'
        f'<div class="leetcode-stats">{stats_html}</div>'
        f'{lang_html}'
        '</div>'
    )

    calendar_html = (
        '\n          <div class="leetcode-cal">'
        '<div class="leetcode-cal__head">'
        '<span data-i18n="leetcode_activity" data-i18n-default="Aktivität">Aktivität</span>'
        '<span class="leetcode-cal__scale">'
        '<span data-i18n="leetcode_cal_less" data-i18n-default="weniger">weniger</span>'
        '<i data-l="0"></i><i data-l="1"></i><i data-l="2"></i><i data-l="3"></i><i data-l="4"></i>'
        '<span data-i18n="leetcode_cal_more" data-i18n-default="mehr">mehr</span>'
        '</span></div>'
        f'<div class="leetcode-cal__grid">{_leetcode_calendar_cells(stats.get("calendar") or {})}</div>'
        '</div>'
    )

    ranking_html = (
        f'<span class="leetcode-card__rank"><span data-i18n="leetcode_ranking" '
        f'data-i18n-default="Ranking">Ranking</span> #{stats["ranking"]:,}</span>'
        if stats.get("ranking") else ''
    )

    return (
        '\n      <section class="section section--tight container reveal" id="leetcode-activity">\n'
        '        <span class="eyebrow" data-i18n="leetcode_eyebrow" data-i18n-default="Live">Live</span>\n'
        '        <h2 class="section__title" data-i18n="leetcode_heading" data-i18n-default="LeetCode">LeetCode</h2>\n'
        f'        <a class="leetcode-card" href="https://leetcode.com/{html_escape(stats["username"])}/" target="_blank" rel="noopener noreferrer">\n'
        '          <div class="leetcode-card__head">\n'
        '            <div class="leetcode-card__headline">\n'
        f'              <span class="leetcode-card__num">{total}</span>\n'
        '              <span class="leetcode-card__unit">\n'
        '                <span class="leetcode-card__unit-main" data-i18n="leetcode_solved" data-i18n-default="gelöst">gelöst</span>\n'
        f'                {ranking_html}\n'
        '              </span>\n'
        '            </div>\n'
        '            <span class="leetcode-card__profile">leetcode.com&nbsp;↗</span>\n'
        '          </div>\n'
        f'          <div class="leetcode-split" role="img" aria-label="Verteilung nach Schwierigkeit">{segs}</div>\n'
        f'          <div class="leetcode-diffs">{diff_rows}</div>'
        f'{group_html}'
        f'{calendar_html}\n'
        '        </a>\n'
        '      </section>\n      '
    )


# Gemeinsame JS-Logik für den Sprachumschalter — identisch auf jeder Seite, die
# lang_switch_html() einbindet. Nutzt zwei Attribute:
# - data-i18n-lang="xx"  auf WRAPPERN mit mehreren Kindern (display:contents,
#   damit der Wrapper selbst keine Box im Flex-/Grid-Layout erzeugt).
# - data-i18n-lang-el="xx" auf einzelnen Elementen (display:revert, damit z.B.
#   ein <p> wieder block wird statt wie ein Wrapper zu verschwinden).
# - data-i18n="key" + data-i18n-default="Deutscher Text" für feste UI-Strings
#   (Buttons/Labels), übersetzt über das Seiten-eigene I18N-Objekt.
LANG_SWITCH_SCRIPT = """
      // Sprache
      const LANG_KEY = 'lang';
      function applyLang(lang) {
        document.documentElement.setAttribute('lang', lang);
        document.querySelectorAll('[data-i18n-lang]').forEach((el) => {
          el.style.display = el.getAttribute('data-i18n-lang') === lang ? 'contents' : 'none';
        });
        document.querySelectorAll('[data-i18n-lang-el]').forEach((el) => {
          el.style.display = el.getAttribute('data-i18n-lang-el') === lang ? 'revert' : 'none';
        });
        document.querySelectorAll('[data-i18n]').forEach((el) => {
          const key = el.getAttribute('data-i18n');
          const fallback = el.getAttribute('data-i18n-default') || el.textContent;
          const dict = (typeof I18N !== 'undefined' && I18N[lang]) || null;
          el.textContent = (lang !== 'de' && dict && dict[key]) || fallback;
        });
        document.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
          const key = el.getAttribute('data-i18n-placeholder');
          const fallback = el.getAttribute('data-i18n-placeholder-default') || el.placeholder;
          const dict = (typeof I18N !== 'undefined' && I18N[lang]) || null;
          el.placeholder = (lang !== 'de' && dict && dict[key]) || fallback;
        });
        localStorage.setItem(LANG_KEY, lang);
        document.querySelectorAll('.lang-switch button').forEach((b) => {
          b.setAttribute('aria-current', b.dataset.lang === lang ? 'true' : 'false');
        });
      }
      function initLang() {
        applyLang(localStorage.getItem(LANG_KEY) || 'de');
      }
      document.querySelectorAll('.lang-switch button').forEach((btn) => {
        btn.addEventListener('click', () => applyLang(btn.dataset.lang));
      });
      initLang();
"""


def js_string(s: str) -> str:
    """Baut einen sicheren JS-String-Literal-Ausdruck (für generierten Code)."""
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def bake_3d_viewer_models(viewer_dir: Path, models_dir: Path | None = None) -> int:
    """Liest alle Dateien aus models_dir (Standard: viewer_dir/modelle) und bettet
    sie base64-codiert in viewer_dir/3d-viewer.html ein (zwischen den
    MODELS_START/END-Markern). Dadurch braucht der Viewer zur Laufzeit weder
    Server noch Datei-Upload — die Modelldaten stecken schon fest im HTML.
    Gibt -1 zurück, wenn die 3d-viewer.html keine Marker hat, sonst die Anzahl
    eingebetteter Dateien."""
    viewer_html = viewer_dir / "3d-viewer.html"
    if not viewer_html.exists():
        return -1

    modelle_dir = models_dir if models_dir is not None else viewer_dir / "modelle"
    entries = []
    if modelle_dir.exists():
        for f in sorted(modelle_dir.iterdir()):
            if not f.is_file():
                continue
            data = base64.b64encode(f.read_bytes()).decode("ascii")
            entries.append(f'{{ name: {js_string(f.name)}, data: {js_string(data)} }}')

    js_array = "[\n      " + ",\n      ".join(entries) + "\n    ]" if entries else "[]"
    src = viewer_html.read_text(encoding="utf-8")
    new_src = replace_marker_block(src, "MODELS", f"const EMBEDDED_MODELS = {js_array};")
    if new_src is None:
        return -1
    viewer_html.write_text(new_src, encoding="utf-8")
    return len(entries)


def apply_viewer_content(viewer_dir: Path, titel: str, text: str) -> bool:
    """Setzt Überschrift und Einleitungstext im 3D-Viewer (zwischen den
    VIEWER_TITLE/VIEWER_TEXT-Markern). Gibt False zurück, wenn die Marker fehlen."""
    viewer_html = viewer_dir / "3d-viewer.html"
    if not viewer_html.exists():
        return False
    src = viewer_html.read_text(encoding="utf-8")
    for marker, value in (("VIEWER_TITLE", html_escape(titel)), ("VIEWER_TEXT", html_escape(text))):
        new_src = replace_marker_block(src, marker, value)
        if new_src is None:
            return False
        src = new_src
    viewer_html.write_text(src, encoding="utf-8")
    return True


def sync_project_3d_viewers() -> None:
    """Kopiert für jedes Projekt mit eigenem modelle/-Ordner eine 3D-Viewer-Kopie
    (HTML + vendor/) aus der Vorlage in "portfolio vorlagen/3d-viewer/" (liegt
    außerhalb dieses Repos) nach projekte/<name>/3d-viewer/, bettet die Modelle
    aus dessen modelle/-Ordner ein und setzt Überschrift/Text aus
    viewer_titel/viewer_text in der projekt.txt (fällt auf title/tagline
    zurück, wenn die nicht gesetzt sind). So bekommt jedes Projekt seinen
    eigenen, in sich geschlossenen Viewer, der komplett im Repo landet."""
    if not VIEWER_SOURCE.exists():
        return
    for project_dir in list_projects():
        modelle_dir = project_dir / "modelle"
        if not modelle_dir.exists() or not any(modelle_dir.iterdir()):
            continue
        target = project_dir / "3d-viewer"
        target.mkdir(exist_ok=True)
        viewer_html = (VIEWER_SOURCE / "3d-viewer.html").read_text(encoding="utf-8")
        # Rücklink anpassen: projekte/<name>/3d-viewer/ liegt drei Ebenen unter
        # dem Repo-Root (anders als die externe Vorlage, deren eigener Rücklink
        # auf portfolio-repo/index.html zeigt).
        viewer_html = viewer_html.replace(
            'href="../../portfolio-repo/index.html"', 'href="../../../index.html"'
        )
        (target / "3d-viewer.html").write_text(viewer_html, encoding="utf-8")
        vendor_target = target / "vendor"
        if vendor_target.exists():
            shutil.rmtree(vendor_target)
        shutil.copytree(VIEWER_SOURCE / "vendor", vendor_target)
        count = bake_3d_viewer_models(target, models_dir=modelle_dir)

        meta = load_project(project_dir)
        kv = parse_kv_block((project_dir / PROJECT_FILE_NAME).read_text(encoding="utf-8")) \
            if (project_dir / PROJECT_FILE_NAME).exists() else {}
        titel = kv.get("viewer_titel") or meta.get("title", project_dir.name)
        text = kv.get("viewer_text") or meta.get("tagline", "")
        apply_viewer_content(target, titel, text)

        print(f"  ✓ {project_dir.name}/3d-viewer/ ({count} Modell(e) eingebettet)")


def list_projects() -> list:
    """Alle Projektordner unter BASE, alphabetisch sortiert.

    Ordner, deren Name mit '_' beginnt (z.B. '_vorlage'), werden übersprungen —
    so kann eine Vorlage im projekte/-Ordner liegen, ohne auf der Website zu
    erscheinen, bis man sie kopiert und umbenennt."""
    if not BASE.exists():
        return []
    return sorted([p for p in BASE.iterdir() if p.is_dir() and not p.name.startswith("_")])


def load_all_projects() -> list:
    """projekt.txt jedes Projekts laden (für Timeline & Übersicht)."""
    projects = []
    for d in list_projects():
        meta = load_project(d)
        projects.append({
            "slug": d.name,
            "dir": d,
            "title": meta["title"],  # immer der Ordnername
            "tagline": meta.get("tagline", ""),
            "year": meta.get("year", ""),
            "stack": meta.get("stack", []),
            "role": meta.get("role", ""),
            "short_description": meta.get("timeline") or meta.get("tagline", ""),
            "featured": meta.get("featured", False),
        })
    return projects


def render_timeline_entry(p: dict) -> str:
    """Ein Timeline-Item. p['href'] ist optional — leer, wenn kein passender
    Projektordner gefunden wurde; der Eintrag erscheint dann ohne Link."""
    href = p.get("href", "")
    title_html = f'<h3 class="timeline__title">{html_escape(p["title"])}</h3>'
    desc_html = f'<p class="timeline__description">{i18n_span_variants(p["short_description"])}</p>'
    if href:
        inner = (
            f'    <a href="{href}" class="timeline__link">\n'
            f'      {title_html}\n'
            f'      {desc_html}\n'
            f'      <span class="timeline__tag" data-i18n="project_tag" data-i18n-default="Projekt">Projekt</span>\n'
            f'    </a>\n'
        )
    else:
        inner = (
            f'    <div class="timeline__link timeline__link--static">\n'
            f'      {title_html}\n'
            f'      {desc_html}\n'
            f'    </div>\n'
        )
    return (
        f'<div class="timeline__item">\n'
        f'  <div class="timeline__dot"></div>\n'
        f'  <div class="timeline__date">{html_escape(str(p["year"]))}</div>\n'
        f'  <div class="timeline__content">\n'
        f'{inner}'
        f'  </div>\n'
        f'</div>'
    )


def render_project_card(p: dict) -> str:
    """Eine Projekt-Karte für alle-projekte.html. Die Beschreibung ist
    p['short_description'] (aus dem 'timeline'-Feld der projekt.txt, fällt
    auf 'tagline' zurück) — dieselbe Quelle wie beim Zeitstrahl-Eintrag."""
    stack_li = "".join(f"<li>{html_escape(s)}</li>" for s in p["stack"])
    year_role = html_escape(f"{p['year']} · {p['role']}".strip(" ·"))
    # Durchsuchbarer Text (Titel/Beschreibung/Stack) fürs clientseitige Live-Suchfeld.
    search_text = html_escape(f"{p['title']} {p['short_description']} {' '.join(p['stack'])}".lower())
    return (
        f'    <li>\n'
        f'      <a class="card reveal" href="projekte/{p["slug"]}/index.html" data-search="{search_text}">\n'
        f'        <div class="card__body">\n'
        f'          <div class="card__year">{year_role}</div>\n'
        f'          <h3 class="card__title">{html_escape(p["title"])}</h3>\n'
        f'          <p class="card__tagline">{i18n_span_variants(p["short_description"])}</p>\n'
        f'          <ul class="card__stack">{stack_li}</ul>\n'
        f'        </div>\n'
        f'        <span class="card__arrow" aria-hidden="true">↗</span>\n'
        f'      </a>\n'
        f'    </li>'
    )


def update_alle_projekte() -> None:
    """Ersetzt die Projekt-Karten zwischen ALL_PROJECTS_START/END in alle-projekte.html."""
    if not ALLE_PROJEKTE.exists():
        print(f"  ! {ALLE_PROJEKTE} nicht gefunden, überspringe alle-projekte.html.")
        return

    projects = load_all_projects()
    cards = "\n".join(render_project_card(p) for p in projects)
    src = ALLE_PROJEKTE.read_text(encoding="utf-8")

    new_src = replace_marker_block(src, "ALL_PROJECTS", cards)
    if new_src is None:
        print("  ! Marker ALL_PROJECTS_START/END fehlen in alle-projekte.html.")
        return
    src = new_src

    i18n_src = replace_marker_block(
        src, "I18N_SCRIPT",
        "const I18N = " + i18n_json([
            "nav_home", "brand_portfolio", "timeline_nav", "all_projects_heading",
            "all_work_eyebrow", "footer_text", "search_placeholder", "search_empty",
            "translation_note",
        ]) + ";",
    )
    if i18n_src is not None:
        src = i18n_src

    ALLE_PROJEKTE.write_text(src, encoding="utf-8")
    print(f"  ✓ {ALLE_PROJEKTE.name} ({len(projects)} Projekte)")


def replace_marker_block(src: str, marker: str, new_inner: str) -> str | None:
    """Ersetzt den Inhalt zwischen <!-- {marker}_START --> und <!-- {marker}_END -->.
    Gibt None zurück, wenn die Marker im Quelltext fehlen."""
    pattern = re.compile(rf"<!--\s*{marker}_START\s*-->.*?<!--\s*{marker}_END\s*-->", re.DOTALL)
    if not pattern.search(src):
        return None
    return pattern.sub(f"<!-- {marker}_START -->\n{new_inner}\n<!-- {marker}_END -->", src)


def update_portfolio_timeline() -> None:
    """Ersetzt den Inhalt zwischen TIMELINE_START und TIMELINE_END in index.html.

    Die Einträge kommen NICHT automatisch von allen Projekten, sondern werden
    von Hand in inhalt.txt unter '## Zeitstrahl' angegeben (siehe
    parse_timeline_section) — so bestimmst du selbst, was im Zeitstrahl
    auftaucht und in welcher Reihenfolge."""
    if not PORTFOLIO.exists():
        print(f"  ! {PORTFOLIO} nicht gefunden, überspringe Timeline.")
        return

    site_text = SITE_CONTENT.read_text(encoding="utf-8") if SITE_CONTENT.exists() else ""
    entries = parse_timeline_section(site_text)

    entries_html = "\n".join(render_timeline_entry(e) for e in entries)
    src = PORTFOLIO.read_text(encoding="utf-8")

    new_src = replace_marker_block(src, "TIMELINE", entries_html)
    if new_src is None:
        print("  ! Marker TIMELINE_START/TIMELINE_END fehlen in index.html.")
        return

    PORTFOLIO.write_text(new_src, encoding="utf-8")
    print(f"  ✓ {PORTFOLIO.name} (Timeline: {len(entries)} Einträge)")


def update_portfolio_content() -> None:
    """Liest inhalt.txt und füllt Hero/About/Skills/Kontakt in index.html.

    inhalt.txt ist die einzige Textdatei, in der Name, Rolle, About-Text,
    Ort, Skills und Kontakt-Links für die Startseite stehen.
    """
    if not PORTFOLIO.exists():
        print(f"  ! {PORTFOLIO} nicht gefunden, überspringe Inhalt.")
        return
    if not SITE_CONTENT.exists():
        print(f"  ! {SITE_CONTENT.name} nicht gefunden, überspringe Inhalt.")
        return

    kv = load_site_content()
    name = html_escape(kv.get("name", "Dein Name"))
    rolle_raw = kv.get("rolle", "")
    ort_raw = kv.get("ort", "")
    about_raw = kv.get("about", "")
    email = kv.get("email", "")
    github = kv.get("github", "")
    linkedin = kv.get("linkedin", "")
    # skills: "Python:90, JavaScript:75, React" — Level (0-100) optional, Default 75.
    skills = []
    for entry in kv.get("skills", "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        skill_name, _, level = entry.partition(":")
        level = level.strip()
        skills.append({
            "name": skill_name.strip(),
            "level": int(level) if level.isdigit() else 75,
        })

    titel = html_escape(kv.get("titel", "Portfolio"))
    meta_beschreibung = html_escape(
        kv.get("meta_beschreibung", "Portfolio — Projekte, Skills und Kontakt auf einen Blick.")
    )
    about_titel_html = i18n_span_variants(kv.get("about_titel", "Kurz über mich"))
    stack_titel_html = i18n_span_variants(kv.get("stack_titel", "Was ich nutze"))
    projekte_titel_html = i18n_span_variants(kv.get("projekte_titel", "Projekte"))
    projekte_text_html = i18n_span_variants(kv.get(
        "projekte_text",
        "Eine chronologische Übersicht aller Projekte findest du im Zeitstrahl oben. Für die komplette Liste:",
    ))
    projekte_button_html = i18n_span_variants(kv.get("projekte_button", "Alle Projekte ansehen →"))
    zeitstrahl_titel_html = i18n_span_variants(kv.get("zeitstrahl_titel", "Zeitstrahl"))
    kontakt_titel_html = i18n_span_variants(kv.get("kontakt_titel", "Lass uns reden"))
    kontakt_text_html = i18n_span_variants(kv.get(
        "kontakt_text", "Du hast was Spannendes? Melde dich gerne per Mail oder über Social."
    ))

    def build_hero(lang):
        rolle_t = html_escape(rolle_raw if lang == "de" else translate_text(rolle_raw, lang))
        about_t = html_escape(about_raw if lang == "de" else translate_text(about_raw, lang))
        kontakt_label = "Kontakt" if lang == "de" else UI_STRINGS["contact_button"][lang]
        return (
            f'<h1 class="hero__name reveal">{name}</h1>\n'
            f'<p class="hero__title reveal">{rolle_t}</p>\n'
            f'<p class="muted reveal" style="max-width: 52ch; margin-bottom: 32px;">{about_t}</p>\n'
            f'<div class="hero__cta reveal">\n'
            f'  <a class="btn btn--ghost" href="mailto:{html_escape(email)}">{kontakt_label}</a>\n'
            f'</div>'
        )
    hero_html = render_i18n_block(build_hero)

    def build_about(lang):
        ort_t = html_escape(ort_raw if lang == "de" else translate_text(ort_raw, lang))
        about_t = html_escape(about_raw if lang == "de" else translate_text(about_raw, lang))
        return f'<div><p class="muted">{ort_t}</p></div>\n<div class="about__copy"><p>{about_t}</p></div>'
    about_html = render_i18n_block(build_about)

    skills_html = render_skill_radar_svg(skills)

    contact_parts = []
    if github:
        contact_parts.append(f'<li><a href="{html_escape(github)}" data-i18n="github_label" data-i18n-default="GitHub">GitHub</a> →</li>')
    if linkedin:
        contact_parts.append(f'<li><a href="{html_escape(linkedin)}" data-i18n="linkedin_label" data-i18n-default="LinkedIn">LinkedIn</a> →</li>')
    if email:
        contact_parts.append(f'<li><a href="mailto:{html_escape(email)}" data-i18n="email_label" data-i18n-default="Email">Email</a> →</li>')
    contact_html = "".join(contact_parts)

    current_project_section = render_current_project_section(kv)

    # Nutzername aus der github:-URL ziehen (leerer Pfad = Platzhalter-URL -> kein Aufruf)
    github_path = urllib.parse.urlparse(github).path.strip("/") if github else ""
    github_username = github_path.split("/")[0] if github_path else ""
    github_activity_section = render_github_activity_section(fetch_github_repos(github_username))
    leetcode_username = kv.get("leetcode", "").strip()
    leetcode_section = render_leetcode_section(fetch_leetcode_stats(leetcode_username))

    src = PORTFOLIO.read_text(encoding="utf-8")
    missing = []
    for marker, inner in (
        ("HERO", hero_html),
        ("ABOUT", about_html),
        ("SKILLS", skills_html),
        ("CONTACT", contact_html),
        ("ABOUT_TITLE", about_titel_html),
        ("STACK_TITLE", stack_titel_html),
        ("PROJECTS_TITLE", projekte_titel_html),
        ("PROJECTS_TEXT", projekte_text_html),
        ("PROJECTS_BUTTON", projekte_button_html),
        ("TIMELINE_TITLE", zeitstrahl_titel_html),
        ("CONTACT_TITLE", kontakt_titel_html),
        ("CONTACT_TEXT", kontakt_text_html),
        ("CURRENT_PROJECT", current_project_section),
        ("GITHUB_ACTIVITY", github_activity_section),
        ("LEETCODE_ACTIVITY", leetcode_section),
    ):
        new_src = replace_marker_block(src, marker, inner)
        if new_src is None:
            missing.append(marker)
            continue
        src = new_src

    # I18N-Objekt für die festen UI-Strings (Home/Portfolio/Eyebrows/Footer/...)
    # dieser Seite — Beschreibungstexte laufen über die data-i18n-lang-Blöcke
    # oben, nicht über dieses Objekt.
    new_src = replace_marker_block(
        src, "I18N_SCRIPT",
        "const I18N = " + i18n_json([
            "nav_home", "brand_portfolio", "about_eyebrow", "stack_eyebrow",
            "all_work_eyebrow", "verlauf_eyebrow", "kontakt_eyebrow", "project_tag",
            "github_label", "linkedin_label", "email_label", "footer_text",
            "current_project_eyebrow", "current_project_link",
            "github_activity_eyebrow", "github_activity_heading", "github_activity_updated",
            "leetcode_eyebrow", "leetcode_heading", "leetcode_solved", "leetcode_ranking",
            "leetcode_easy", "leetcode_medium", "leetcode_hard",
            "leetcode_acceptance", "leetcode_streak", "leetcode_active_days",
            "leetcode_languages", "leetcode_activity", "leetcode_cal_less", "leetcode_cal_more",
            "translation_note",
        ]) + ";",
    )
    if new_src is None:
        missing.append("I18N_SCRIPT")
    else:
        src = new_src

    # <title> und Meta-Description sind RCDATA/Attribut-Inhalte, dort funktionieren
    # HTML-Kommentar-Marker nicht — deshalb direkter Tag-Ersatz.
    src, n_title = re.subn(r"<title>.*?</title>", f"<title>{titel}</title>", src, count=1)
    if not n_title:
        missing.append("TITLE")
    src, n_meta = re.subn(
        r'(<meta name="description" content=")[^"]*("\s*/>)',
        rf"\1{meta_beschreibung}\2",
        src, count=1,
    )
    if not n_meta:
        missing.append("META_DESCRIPTION")

    PORTFOLIO.write_text(src, encoding="utf-8")
    if missing:
        print(f"  ! Marker fehlen in index.html: {', '.join(missing)}")
    print(f"  ✓ {PORTFOLIO.name} (Inhalt aus {SITE_CONTENT.name})")


def main() -> None:
    print(f"Base: {BASE}")
    if not BASE.exists():
        print("  ! projekte/-Ordner fehlt.")
        return

    projects = list_projects()
    if not projects:
        print("  ! Keine Projektordner gefunden.")
        return

    print(f"\nProjekte ({len(projects)}):")
    for p in projects:
        update_project(p)

    print("\nPortfolio:")
    update_portfolio_content()
    update_portfolio_timeline()
    update_alle_projekte()

    if VIEWER_SOURCE.exists():
        print("\n3D-Viewer:")
        count = bake_3d_viewer_models(VIEWER_SOURCE)
        viewer_content_file = VIEWER_SOURCE / "inhalt.txt"
        if viewer_content_file.exists():
            kv = parse_kv_block(viewer_content_file.read_text(encoding="utf-8"))
            apply_viewer_content(
                VIEWER_SOURCE,
                kv.get("titel", "3D-Modell-Viewer"),
                kv.get("text", ""),
            )
        print(f"  ✓ portfolio vorlagen/3d-viewer/3d-viewer.html ({count} Modell(e) eingebettet)")
        sync_project_3d_viewers()

    save_translation_cache()


if __name__ == "__main__":
    main()