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
import re
import shutil
from pathlib import Path

BASE = Path(__file__).parent / "projekte"
PORTFOLIO = Path(__file__).parent / "index.html"
ALLE_PROJEKTE = Path(__file__).parent / "alle-projekte.html"
SITE_CONTENT = Path(__file__).parent / "inhalt.txt"
PROJECT_FILE_NAME = "projekt.txt"
# Einzige Quelle für den 3D-Viewer ist die Vorlage im eigenen Repo-Ordner
# "3d-viewer-vorlage/" — von dort kopiert sync_project_3d_viewers() pro Projekt
# eine eigene Kopie nach projekte/<name>/3d-viewer/.
VIEWER_SOURCE = Path(__file__).parent / "3d-viewer-vorlage"


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


def render_footer_links_html(site: dict) -> str:
    """Kontakt-Links (GitHub/LinkedIn/Email aus inhalt.txt) für den Footer der Projektseiten."""
    parts = []
    if site.get("github"):
        parts.append(f'<li><a href="{html_escape(site["github"])}" target="_blank" rel="noopener noreferrer">GitHub</a></li>')
    if site.get("linkedin"):
        parts.append(f'<li><a href="{html_escape(site["linkedin"])}" target="_blank" rel="noopener noreferrer">LinkedIn</a></li>')
    if site.get("email"):
        parts.append(f'<li><a href="mailto:{html_escape(site["email"])}">Email</a></li>')
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


def parse_project_file(path: Path, fallback_title: str = "") -> dict:
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
    """
    if not path.exists():
        return {
            "title": fallback_title,
            "tagline": "", "role": "", "year": "", "stack": [],
            "links": {"repo": "", "live": "", "3d-viewer": ""}, "featured": False,
            "timeline": "", "description": "", "images": [], "files": [], "custom_links": [],
        }

    text = path.read_text(encoding="utf-8")
    header_block, _, rest = text.partition("\n\n")
    kv = parse_kv_block(header_block)

    section_pattern = re.compile(r'^##\s*(.+?)\s*$', re.MULTILINE)
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
        "title": kv.get("title", fallback_title),
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
    allen Texten, Überschriften, Links und Bildern dieses Projekts)."""
    return parse_project_file(project_dir / PROJECT_FILE_NAME, fallback_title=project_dir.name)


VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "m4v", "ogv"}


def render_gallery(images: list) -> str:
    """Rendert die Bilder-/Video-Galerie als <li>-Liste.

    Einträge mit einer Video-Dateiendung (mp4/webm/mov/m4v/ogv) werden als
    abspielbares <video controls> gerendert, alle anderen als <img>."""
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

        items.append(
            f'<li class="gallery__item">'
            f'{media}'
            f'<div class="gallery__caption">'
            f'<div class="gallery__caption-title">{caption or alt}</div>'
            f'</div></li>'
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
            '<a class="btn btn--primary" href="details.html">Demo ansehen</a>'
        )
    if links.get("repo"):
        parts.append(
            f'<a class="btn btn--ghost" href="{html_escape(links["repo"])}" '
            f'target="_blank" rel="noopener noreferrer">Source</a>'
        )
    if links.get("live"):
        parts.append(
            f'<a class="btn btn--primary" href="{html_escape(links["live"])}" '
            f'target="_blank" rel="noopener noreferrer">Live</a>'
        )
    if links.get("3d-viewer"):
        parts.append(
            f'<a class="btn btn--primary" href="{html_escape(links["3d-viewer"])}" '
            f'target="_blank" rel="noopener noreferrer">3D-Ansicht</a>'
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
    body_html = parse_description(meta.get("description", ""))
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
            '        <span class="eyebrow">Einblicke</span>\n'
            '        <h2 class="section__title">Bilder</h2>\n'
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
            '        <span class="eyebrow">Downloads</span>\n'
            '        <h2 class="section__title">Dateien</h2>\n'
            f'        <ul class="files">{render_files_list(downloadable_files)}</ul>\n'
            '      </section>'
        )

    links_html = render_links_html(meta, project_dir)

    eyebrow = html_escape(f"{meta.get('role', '')} · {meta.get('year', '')}".strip(" ·"))
    title = html_escape(meta.get("title", project_dir.name))
    tagline = html_escape(meta.get("tagline", ""))
    stack_html = " ".join(f"<span>· {html_escape(s)}</span>" for s in meta.get("stack", []))
    skills_html = "".join(f"<li>{html_escape(s)}</li>" for s in meta.get("stack", []))

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
      <a href="../../index.html" class="brand" data-home>Portfolio</a>
      <nav class="nav" aria-label="Hauptnavigation">
        <a class="nav__link" href="../../index.html" data-home>Home</a>
        <button class="theme-toggle" data-theme-toggle aria-label="Theme wechseln">◐</button>
      </nav>
    </div>
  </header>

  <main id="main">
    <article class="section container detail">
      <a class="detail__back" href="../../index.html" data-home>← Zurück zum Portfolio</a>
      <header class="detail__header">
        <span class="eyebrow reveal">{eyebrow}</span>
        <h1 class="reveal">{title}</h1>
        <p class="hero__title reveal">{tagline}</p>
        <div class="detail__meta reveal">{stack_html}</div>
        <ul class="skills" aria-label="Tech Stack" style="margin-top: 16px;">{skills_html}</ul>
        <div class="detail__links reveal">{links_html}</div>
      </header>
      <div class="detail__body reveal" id="description">{body_html}</div>{files_section}{gallery_section}
    </article>
  </main>

  <footer class="site-footer">
    <div class="container footer__inner">
      <span>© 2026 — gebaut mit Vite, HTML & CSS.</span>
      <ul class="footer__links">{footer_links_html}</ul>
    </div>
  </footer>
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
        stack_html=stack_html,
        skills_html=skills_html,
        body_html=body_html,
        files_section=files_section,
        gallery_section=gallery_section,
        links_html=links_html,
        footer_links_html=footer_links_html,
    )

    return page


def update_project(project_dir: Path) -> None:
    """Schreibt die index.html eines Projektordners neu."""
    html = build_project_index(project_dir)
    (project_dir / "index.html").write_text(html, encoding="utf-8")
    print(f"  ✓ {project_dir.name}/index.html")


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
    (HTML + vendor/) aus der Vorlage in "3d-viewer-vorlage/" nach
    projekte/<name>/3d-viewer/, bettet die Modelle aus dessen modelle/-Ordner ein
    und setzt Überschrift/Text aus viewer_titel/viewer_text in der projekt.txt
    (fällt auf title/tagline zurück, wenn die nicht gesetzt sind). So bekommt
    jedes Projekt seinen eigenen, in sich geschlossenen Viewer."""
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
        # dem Repo-Root (anders als die Vorlage selbst, die nur eine Ebene tief liegt).
        viewer_html = viewer_html.replace(
            'href="../index.html"', 'href="../../../index.html"'
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
            "title": meta.get("title", d.name),
            "tagline": meta.get("tagline", ""),
            "year": meta.get("year", ""),
            "stack": meta.get("stack", []),
            "role": meta.get("role", ""),
            "short_description": meta.get("timeline") or meta.get("tagline", ""),
            "featured": meta.get("featured", False),
        })
    return projects


def render_timeline_entry(p: dict) -> str:
    """Ein Timeline-Item."""
    href = f'projekte/{p["slug"]}/index.html'
    return (
        f'<div class="timeline__item">\n'
        f'  <div class="timeline__dot"></div>\n'
        f'  <div class="timeline__date">{html_escape(str(p["year"]))}</div>\n'
        f'  <div class="timeline__content">\n'
        f'    <a href="{href}" class="timeline__link">\n'
        f'      <h3 class="timeline__title">{html_escape(p["title"])}</h3>\n'
        f'      <p class="timeline__description">{html_escape(p["short_description"])}</p>\n'
        f'      <span class="timeline__tag">Projekt</span>\n'
        f'    </a>\n'
        f'  </div>\n'
        f'</div>'
    )


def render_project_card(p: dict) -> str:
    """Eine Projekt-Karte für alle-projekte.html."""
    stack_li = "".join(f"<li>{html_escape(s)}</li>" for s in p["stack"])
    year_role = html_escape(f"{p['year']} · {p['role']}".strip(" ·"))
    return (
        f'    <li>\n'
        f'      <a class="card reveal" href="projekte/{p["slug"]}/index.html" >\n'
        f'        <div class="card__body">\n'
        f'          <div class="card__year">{year_role}</div>\n'
        f'          <h3 class="card__title">{html_escape(p["title"])}</h3>\n'
        f'          <p class="card__tagline">{html_escape(p["tagline"])}</p>\n'
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

    ALLE_PROJEKTE.write_text(new_src, encoding="utf-8")
    print(f"  ✓ {ALLE_PROJEKTE.name} ({len(projects)} Projekte)")


def replace_marker_block(src: str, marker: str, new_inner: str) -> str | None:
    """Ersetzt den Inhalt zwischen <!-- {marker}_START --> und <!-- {marker}_END -->.
    Gibt None zurück, wenn die Marker im Quelltext fehlen."""
    pattern = re.compile(rf"<!--\s*{marker}_START\s*-->.*?<!--\s*{marker}_END\s*-->", re.DOTALL)
    if not pattern.search(src):
        return None
    return pattern.sub(f"<!-- {marker}_START -->\n{new_inner}\n<!-- {marker}_END -->", src)


def update_portfolio_timeline() -> None:
    """Ersetzt den Inhalt zwischen TIMELINE_START und TIMELINE_END in index.html."""
    if not PORTFOLIO.exists():
        print(f"  ! {PORTFOLIO} nicht gefunden, überspringe Timeline.")
        return

    projects = load_all_projects()
    # älteste zuerst; fehlende Jahre nach hinten
    projects.sort(key=lambda p: (p["year"] == "", int(p["year"]) if str(p["year"]).isdigit() else 0))

    entries = "\n".join(render_timeline_entry(p) for p in projects)
    src = PORTFOLIO.read_text(encoding="utf-8")

    new_src = replace_marker_block(src, "TIMELINE", entries)
    if new_src is None:
        print("  ! Marker TIMELINE_START/TIMELINE_END fehlen in index.html.")
        return

    PORTFOLIO.write_text(new_src, encoding="utf-8")
    print(f"  ✓ {PORTFOLIO.name} (Timeline: {len(projects)} Einträge)")


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
    rolle = html_escape(kv.get("rolle", ""))
    ort = html_escape(kv.get("ort", ""))
    about = html_escape(kv.get("about", ""))
    email = kv.get("email", "")
    github = kv.get("github", "")
    linkedin = kv.get("linkedin", "")
    skills = [s.strip() for s in kv.get("skills", "").split(",") if s.strip()]

    titel = html_escape(kv.get("titel", "Portfolio"))
    meta_beschreibung = html_escape(
        kv.get("meta_beschreibung", "Portfolio — Projekte, Skills und Kontakt auf einen Blick.")
    )
    about_titel = html_escape(kv.get("about_titel", "Kurz über mich"))
    stack_titel = html_escape(kv.get("stack_titel", "Was ich nutze"))
    projekte_titel = html_escape(kv.get("projekte_titel", "Projekte"))
    projekte_text = html_escape(kv.get(
        "projekte_text",
        "Eine chronologische Übersicht aller Projekte findest du im Zeitstrahl oben. Für die komplette Liste:",
    ))
    projekte_button = html_escape(kv.get("projekte_button", "Alle Projekte ansehen →"))
    zeitstrahl_titel = html_escape(kv.get("zeitstrahl_titel", "Zeitstrahl"))
    kontakt_titel = html_escape(kv.get("kontakt_titel", "Lass uns reden"))
    kontakt_text = html_escape(kv.get(
        "kontakt_text", "Du hast was Spannendes? Melde dich gerne per Mail oder über Social."
    ))

    hero_html = (
        f'<h1 class="hero__name reveal">{name}</h1>\n'
        f'<p class="hero__title reveal">{rolle}</p>\n'
        f'<p class="muted reveal" style="max-width: 52ch; margin-bottom: 32px;">{about}</p>\n'
        f'<div class="hero__cta reveal">\n'
        f'  <a class="btn btn--ghost" href="mailto:{html_escape(email)}">Kontakt</a>\n'
        f'</div>'
    )
    about_html = (
        f'<div><p class="muted">{ort}</p></div>\n'
        f'<div class="about__copy"><p>{about}</p></div>'
    )
    skills_html = "".join(f'<li>{html_escape(s)}</li>' for s in skills)

    contact_parts = []
    if github:
        contact_parts.append(f'<li><a href="{html_escape(github)}">GitHub →</a></li>')
    if linkedin:
        contact_parts.append(f'<li><a href="{html_escape(linkedin)}">LinkedIn →</a></li>')
    if email:
        contact_parts.append(f'<li><a href="mailto:{html_escape(email)}">Email →</a></li>')
    contact_html = "".join(contact_parts)

    src = PORTFOLIO.read_text(encoding="utf-8")
    missing = []
    for marker, inner in (
        ("HERO", hero_html),
        ("ABOUT", about_html),
        ("SKILLS", skills_html),
        ("CONTACT", contact_html),
        ("ABOUT_TITLE", about_titel),
        ("STACK_TITLE", stack_titel),
        ("PROJECTS_TITLE", projekte_titel),
        ("PROJECTS_TEXT", projekte_text),
        ("PROJECTS_BUTTON", projekte_button),
        ("TIMELINE_TITLE", zeitstrahl_titel),
        ("CONTACT_TITLE", kontakt_titel),
        ("CONTACT_TEXT", kontakt_text),
    ):
        new_src = replace_marker_block(src, marker, inner)
        if new_src is None:
            missing.append(marker)
            continue
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
        print(f"  ✓ 3d-viewer-vorlage/3d-viewer.html ({count} Modell(e) eingebettet)")
        sync_project_3d_viewers()


if __name__ == "__main__":
    main()