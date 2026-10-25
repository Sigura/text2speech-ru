"""Build the reader fixtures (committed; re-run after changing this file).

    uv run python tests/fixtures/build.py

The canonical Russian sample is an excerpt of Pushkin's «Капитанская дочка» (1836,
public domain; text from ru.wikisource, 1960 edition): chapter epigraphs in verse with
attribution, editor's footnotes, emphasis, «» quotes, a French phrase and dialogue.
Each format is written by its own small writer here, independent of the readers.
Output is deterministic (fixed ZIP timestamps), so a rebuild does not churn git.
"""

from __future__ import annotations

import struct
import zipfile
import zlib
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).parent
ZIP_DATE = (2020, 1, 1, 0, 0, 0)

# -- canonical book -------------------------------------------------------------
# Inline markup in the strings below: *emphasis*, [^id] footnote reference.

TITLE = "Капитанская дочка"
AUTHOR = ("Александр", "Сергеевич", "Пушкин")
NOTES = {
    "n1": "Эпиграф взят из комедии Якова Княжнина «Хвастун» (*прим. редактора*).",
    "n2": "чтобы стать учителем (*франц.*).",
}
CHAPTERS = [
    {
        "title": ("Глава I", "Сержант гвардии"),
        "epigraph": [
            "— Был бы гвардии он завтра ж капитан.",
            "— Того не надобно; пусть в армии послужит.",
            "— Изрядно сказано! пускай его потужит…",
            "Да кто его отец?",
        ],
        "epigraph_author": "Княжнин.[^n1]",
        "paragraphs": [
            "Отец мой Андрей Петрович Гринев в молодости своей служил при графе Минихе и "
            "вышел в отставку премьер-майором в 17.. году. С тех пор жил он в своей "
            "Симбирской деревне, где и женился на девице Авдотье Васильевне Ю., дочери "
            "бедного тамошнего дворянина. Нас было девять человек детей. Все мои братья и "
            "сестры умерли во младенчестве.",
            "Бопре в отечестве своем был парикмахером, потом в Пруссии солдатом, потом "
            "приехал в Россию pour être outchitel[^n2], не очень понимая значение этого "
            "слова. Он был добрый малый, но ветрен и беспутен до крайности. К тому же не был "
            "он (по его выражению) и *врагом бутылки*, т. е. (говоря по-русски) любил "
            "хлебнуть лишнее.",
        ],
    },
    {
        "title": ("Глава II", "Вожатый"),
        "epigraph": [
            "Сторона ль моя, сторонушка,",
            "Сторона незнакомая!",
            "Что не сам ли я на тебя зашел,",
            "Что не добрый ли да меня конь завез:",
            "Завезла меня, доброго молодца,",
            "Прытость, бодрость молодецкая",
            "И хмелинушка кабацкая.",
        ],
        "epigraph_author": "Старинная песня",
        "paragraphs": [
            "Дорожные размышления мои были не очень приятны. Проигрыш мой, по тогдашним "
            "ценам, был немаловажен. Наконец я сказал ему: «Ну, ну, Савельич! полно, "
            "помиримся, виноват; вижу сам, что виноват».",
            "— Эх, батюшка Петр Андреич! — отвечал он с глубоким вздохом. — Сержусь-то я "
            "на самого себя; сам я кругом виноват.",
            "— Это зачем?",
            "— Время ненадежно: ветер слегка подымается; вишь, как он сметает порошу.",
            "— Что ж за беда!",
            "— А вон — вон: это облачко.",
        ],
    },
]


def tiny_png() -> bytes:
    """A 4×4 dark-red PNG, made without image libraries."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + b"\x80\x10\x10" * 4 for _ in range(4))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def zip_bytes(members: list[tuple[str, bytes]], *, stored: tuple[str, ...] = ()) -> bytes:
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members:
            info = zipfile.ZipInfo(name, ZIP_DATE)
            info.compress_type = zipfile.ZIP_STORED if name in stored else zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return buf.getvalue()


# -- FB2 -------------------------------------------------------------------------


def fb2_inline(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        if text.startswith("[^", i):
            end = text.index("]", i)
            note = text[i + 2 : end]
            out.append(f'<a l:href="#{note}" type="note">[{note[1:]}]</a>')
            i = end + 1
        elif text[i] == "*":
            end = text.index("*", i + 1)
            out.append(f"<emphasis>{escape(text[i + 1 : end])}</emphasis>")
            i = end + 1
        else:
            j = min(
                (k for k in (text.find("[^", i), text.find("*", i)) if k != -1), default=len(text)
            )
            out.append(escape(text[i:j]))
            i = j
    return "".join(out)


def fb2_book() -> str:
    first, middle, last = AUTHOR
    sections = []
    for ch in CHAPTERS:
        lines = "".join(f"<v>{fb2_inline(v)}</v>" for v in ch["epigraph"])
        paras = "".join(f"<p>{fb2_inline(p)}</p>" for p in ch["paragraphs"])
        title = "".join(f"<p>{escape(t)}</p>" for t in ch["title"])
        sections.append(
            f"<section><title>{title}</title>"
            f"<epigraph><poem><stanza>{lines}</stanza></poem>"
            f"<text-author>{fb2_inline(ch['epigraph_author'])}</text-author></epigraph>"
            f"{paras}</section>"
        )
    notes = "".join(
        f'<section id="{k}"><title><p>{k[1:]}</p></title><p>{fb2_inline(v)}</p></section>'
        for k, v in NOTES.items()
    )
    cover = zlib_free_base64(tiny_png())
    return (
        '<?xml version="1.0" encoding="windows-1251"?>\n'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
        'xmlns:l="http://www.w3.org/1999/xlink">'
        "<description><title-info><genre>prose_rus_classic</genre>"
        f"<author><first-name>{first}</first-name><middle-name>{middle}</middle-name>"
        f"<last-name>{last}</last-name></author>"
        f"<book-title>{TITLE}</book-title>"
        "<annotation><p>Исторический роман о пугачевском восстании.</p></annotation>"
        '<date value="1836-01-01">1836</date>'
        '<coverpage><image l:href="#cover.png"/></coverpage><lang>ru</lang>'
        '<sequence name="Повести и романы" number="7"/>'
        "</title-info></description>"
        f"<body><title><p>{AUTHOR[0]} {AUTHOR[2]}</p><p>{TITLE}</p></title>"
        f"{''.join(sections)}</body>"
        f'<body name="notes"><title><p>Примечания</p></title>{notes}</body>'
        f'<binary id="cover.png" content-type="image/png">{cover}</binary>'
        "</FictionBook>\n"
    )


def zlib_free_base64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


# Modelled on a real library FB2: ad banners in non-standard elements, POV subtitles,
# emphasis split per word with spaces inside, en dash + NBSP dialogue, an emphasised
# dash, a Latin homoglyph word and a two-line body title.
QUIRKS_FB2 = (
    '<?xml version="1.0" encoding="windows-1251"?>\n'
    '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
    'xmlns:l="http://www.w3.org/1999/xlink"><description><title-info><genre>sf</genre>'
    "<author><first-name>Иван</first-name><last-name>Тестов</last-name></author>"
    "<book-title>Станция</book-title><lang>ru</lang>"
    "<translator><first-name>П.</first-name><last-name>Переводчиков</last-name></translator>"
    "</title-info></description><body>"
    '<myheader><p>Спасибо, что скачали книгу в <a l:href="http://royallib.ru">'
    "бесплатной электронной библиотеке Royallib.ru</a></p><empty-line/></myheader>"
    "<title><p>Иван Тестов</p><p>Станция</p></title>"
    "<section><title><p>ПРОЛОГ</p></title><subtitle>Рукав Стрельца</subtitle><empty-line/>"
    "<p><emphasis>Есть. </emphasis>Зонд ждал.</p></section>"
    "<section><title><p>ГЛАВА 1</p></title><subtitle>Сэм</subtitle><empty-line/>"
    "<p>Он сказал это <emphasis>как </emphasis><emphasis>будто </emphasis>шутя.</p>"
    "<p>– Скажи мне, кто это сделал, – попросил он.</p>"
    "<p><emphasis>–</emphasis> Убить меня – не значит отблагодарить.</p>"
    "<subtitle>У pcy</subtitle><p>Конец главы.</p></section>"
    "<myfooter><p>Оставить отзыв о книге на Royallib.ru</p></myfooter>"
    "</body></FictionBook>\n"
)

# The whole book inside one section whose title repeats author and title (seen in a
# real FB2), parts → chapters below it, and "* * *" scene breaks as <subtitle> and <p>.
WRAPPED_FB2 = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
    "<description><title-info><author><first-name>Анна</first-name>"
    "<last-name>Обёрткина</last-name></author><book-title>Книга в обёртке</book-title>"
    "<lang>ru</lang></title-info></description><body>"
    "<title><p>Книга в обёртке</p></title>"
    "<section><title><p>Анна Обёрткина. КНИГА В ОБЁРТКЕ,</p><p>ИЛИ РОМАН</p></title>"
    "<section><title><p>Часть первая</p></title>"
    "<section><title><p>Глава 1</p></title><p>Начало.</p><subtitle>* * *</subtitle>"
    "<p>После перерыва.</p></section>"
    "<section><title><p>Глава 2</p></title><p>Дальше.</p><p>***</p><p>Ещё.</p></section>"
    "</section>"
    "<section><title><p>Часть вторая</p></title>"
    "<section><title><p>Глава 3</p></title><p>Конец.</p></section></section>"
    "</section></body></FictionBook>\n"
)

XXE_FB2 = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<!DOCTYPE FictionBook [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
    '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
    "<body><section><p>&xxe;</p></section></body></FictionBook>\n"
)

LAUGHS_FB2 = (
    '<?xml version="1.0"?>\n<!DOCTYPE lolz [<!ENTITY lol "lol">'
    '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
    '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
    "<body><section><p>&lol2;</p></section></body></FictionBook>\n"
)


# -- EPUB ----------------------------------------------------------------------------

CONTAINER = (
    '<?xml version="1.0"?>\n<container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
    '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
    "</rootfiles></container>\n"
)


def xhtml(title: str, body: str, *, epub3: bool = True) -> str:
    ns = ' xmlns:epub="http://www.idpf.org/2007/ops"' if epub3 else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml"{ns}><head><title>{escape(title)}</title>'
        f"</head><body>{body}</body></html>\n"
    )


def html_inline(text: str, note_link: str) -> str:
    """*x* → <em>, [^id] → a note link built by `note_link(id)`."""
    import re

    out = escape(text)
    out = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", out)
    return re.sub(r"\[\^(\w+)\]", lambda m: note_link(m.group(1)), out)


def epub3_book() -> bytes:
    first, _, last = AUTHOR

    def ref(n: str) -> str:
        return f'<a epub:type="noteref" href="#{n}">{n[1:]}</a>'

    chapters = []
    for i, ch in enumerate(CHAPTERS, 1):
        title = ". ".join(ch["title"])
        lines = "<br/>".join(escape(v) for v in ch["epigraph"])
        notes = [n for n in NOTES if f"[^{n}]" in str(ch)]
        body = (
            f'<section epub:type="chapter"><h2>{escape(title)}</h2>'
            f'<blockquote class="epigraph"><p class="poem">{lines}</p>'
            f'<p class="text-author">{html_inline(ch["epigraph_author"], ref)}</p></blockquote>'
            + "".join(f"<p>{html_inline(p, ref)}</p>" for p in ch["paragraphs"])
            + "".join(
                f'<aside epub:type="footnote" id="{n}"><p>{html_inline(NOTES[n], ref)}</p></aside>'
                for n in notes
            )
            + "</section>"
        )
        chapters.append((f"ch{i}.xhtml", xhtml(title, body.replace(" т. е.", "&nbsp;т.&nbsp;е."))))
    nav_items = "".join(
        f'<li><a href="ch{i}.xhtml">{escape(". ".join(ch["title"]))}</a></li>'
        for i, ch in enumerate(CHAPTERS, 1)
    )
    nav = xhtml("Оглавление", f'<nav epub:type="toc"><h1>Оглавление</h1><ol>{nav_items}</ol></nav>')
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="id">urn:uuid:00000000-0000-0000-0000-000000001836</dc:identifier>'
        f"<dc:title>{TITLE}</dc:title><dc:creator>{first} {last}</dc:creator>"
        "<dc:language>ru-RU</dc:language><dc:date>1836</dc:date>"
        "<dc:subject>Историческая проза</dc:subject>"
        '<meta property="belongs-to-collection" id="c1">Повести и романы</meta>'
        '<meta refines="#c1" property="group-position">7</meta>'
        "</metadata><manifest>"
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        '<item id="cover" href="images/cover.png" media-type="image/png" '
        'properties="cover-image"/>'
        + "".join(
            f'<item id="c{i}" href="ch{i}.xhtml" media-type="application/xhtml+xml"/>'
            for i in range(1, len(chapters) + 1)
        )
        + '</manifest><spine><itemref idref="nav" linear="no"/>'
        + "".join(f'<itemref idref="c{i}"/>' for i in range(1, len(chapters) + 1))
        + "</spine></package>\n"
    )
    members = [
        ("mimetype", b"application/epub+zip"),
        ("META-INF/container.xml", CONTAINER.encode()),
        ("OEBPS/content.opf", opf.encode()),
        ("OEBPS/nav.xhtml", nav.encode()),
        ("OEBPS/images/cover.png", tiny_png()),
        *[(f"OEBPS/{name}", text.encode()) for name, text in chapters],
    ]
    return zip_bytes(members, stored=("mimetype",))


def epub2_book() -> bytes:
    """EPUB 2: NCX, one text file with anchors, notes in their own file, stanza divs."""
    first, _, last = AUTHOR

    def ref(n: str) -> str:
        return f'<a id="r{n}" href="notes.xhtml#{n}"><sup>{n[1:]}</sup></a>'

    parts = []
    for i, ch in enumerate(CHAPTERS, 1):
        lines = "".join(f"<p>{escape(v)}</p>" for v in ch["epigraph"])
        parts.append(
            f'<h2 id="ch{i}">{escape(ch["title"][0])}<br/>{escape(ch["title"][1])}</h2>'
            f'<div class="epigraph"><div class="stanza">{lines}</div>'
            f'<p class="text-author">— {html_inline(ch["epigraph_author"], ref)}</p></div>'
            + "".join(f'<p class="text">{html_inline(p, ref)}</p>' for p in ch["paragraphs"])
        )
    text = xhtml(TITLE, "".join(parts), epub3=False)
    notes_body = "<h2>Примечания</h2>" + "".join(
        f'<p id="{n}"><a href="text.xhtml#r{n}">{n[1:]}</a> {html_inline(v, ref)}</p>'
        for n, v in NOTES.items()
    )
    notes = xhtml("Примечания", notes_body, epub3=False)
    nav_points = "".join(
        f'<navPoint id="p{i}" playOrder="{i}"><navLabel><text>{escape(". ".join(ch["title"]))}'
        f'</text></navLabel><content src="text.xhtml#ch{i}"/></navPoint>'
        for i, ch in enumerate(CHAPTERS, 1)
    )
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
        f'version="2005-1"><head/><docTitle><text>{TITLE}</text></docTitle>'
        f"<navMap>{nav_points}</navMap></ncx>\n"
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:opf="http://www.idpf.org/2007/opf">'
        '<dc:identifier id="id" opf:scheme="ISBN">978-5-00-000000-0</dc:identifier>'
        f'<dc:title>{TITLE}</dc:title><dc:creator opf:role="aut">{first} {last}</dc:creator>'
        "<dc:language>ru</dc:language>"
        '<meta name="cover" content="cover-img"/>'
        '<meta name="calibre:series" content="Повести и романы"/>'
        '<meta name="calibre:series_index" content="7.0"/>'
        "</metadata><manifest>"
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        '<item id="cover-img" href="cover.png" media-type="image/png"/>'
        '<item id="text" href="text.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest><spine toc="ncx"><itemref idref="text"/><itemref idref="notes"/></spine>'
        "</package>\n"
    )
    members = [
        ("mimetype", b"application/epub+zip"),
        ("META-INF/container.xml", CONTAINER.encode()),
        ("OEBPS/content.opf", opf.encode()),
        ("OEBPS/toc.ncx", ncx.encode()),
        ("OEBPS/cover.png", tiny_png()),
        ("OEBPS/text.xhtml", text.encode()),
        ("OEBPS/notes.xhtml", notes.encode()),
    ]
    return zip_bytes(members, stored=("mimetype",))


ALICE = [
    "Alice was beginning to get very tired of sitting by her sister on the bank, and of "
    "having nothing to do: once or twice she had peeped into the book her sister was "
    "reading, but it had no pictures or conversations in it, “and what is the use of a "
    "book,” thought Alice “without pictures or conversations?”",
    "So she was considering in her own mind (as well as she could, for the hot day made "
    "her feel very sleepy and stupid), whether the pleasure of making a daisy-chain would "
    "be worth the trouble of getting up and picking the daisies, when suddenly a White "
    "Rabbit with pink eyes ran close by her.",
]


def epub_english() -> bytes:
    body = '<h1 class="chapter">CHAPTER I.<br/>Down the Rabbit-Hole</h1>' + "".join(
        f"<p>{escape(p)}</p>" for p in ALICE
    )
    chapter = xhtml("Down the Rabbit-Hole", body)
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="id">alice</dc:identifier>'
        "<dc:title>Alice’s Adventures in Wonderland</dc:title>"
        "<dc:creator>Lewis Carroll</dc:creator><dc:language>en</dc:language></metadata>"
        '<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest><spine><itemref idref="c1"/></spine></package>\n'
    )
    members = [
        ("mimetype", b"application/epub+zip"),
        ("META-INF/container.xml", CONTAINER.encode()),
        ("OEBPS/content.opf", opf.encode()),
        ("OEBPS/c1.xhtml", chapter.encode()),
    ]
    return zip_bytes(members, stored=("mimetype",))


# -- TXT and MD ---------------------------------------------------------------------


def _strip_markup(text: str) -> str:
    import re

    return re.sub(r"\[\^\w+\]", "", text).replace("*", "")


def txt_librus() -> str:
    """lib.ru layout: indented, hard-wrapped paragraphs; chapter title on the next line."""
    import textwrap

    first, _, last = AUTHOR
    out = [f"{first} {last}", TITLE, ""]
    for ch in CHAPTERS:
        number, name = ch["title"]
        out += ["", f"     {number}", f"     {name.upper()}", ""]
        out += [f"                    {_strip_markup(v)}" for v in ch["epigraph"]]
        out += [f"                              {_strip_markup(ch['epigraph_author'])}", ""]
        for p in ch["paragraphs"]:
            lines = textwrap.wrap(_strip_markup(p), width=66)
            out += ["     " + lines[0], *lines[1:]]
    # cp1251 has no "ê"; real cp1251 texts write a plain "e".
    return ("\r\n".join(out) + "\r\n").replace("ê", "e")


BLANK_LINES_TXT = """Содержание

Часть первая
Глава 1. Начало
Глава 2. Дорога
Часть вторая
Глава 3. Конец

Часть первая

Глава 1. Начало

Первый абзац первой главы. Он достаточно длинный, чтобы считаться прозой, а не \
строкой оглавления, и продолжается на следующей строке без пустой строки между ними.
Вторая строка того же абзаца с перено-
сом слова.

* * *

После перерыва.

Глава 2. Дорога

Дорога шла через лес.

Часть вторая

Глава 3. Конец

Конец книги.
"""


def md_book() -> str:
    """The canonical book in the §6.4.1 dialect, written by hand (not by our writer)."""
    first, _, last = AUTHOR
    out = [f"# {first} {last}. {TITLE}", ""]
    for ch in CHAPTERS:
        out += [f"## {ch['title'][0]}. {ch['title'][1]}", ""]
        out.append("\\\n".join(f"> {v}" for v in ch["epigraph"]))
        out += [">", f"> — {ch['epigraph_author']}".replace("[^n", "[^"), ""]
        for p in ch["paragraphs"]:
            out += [p.replace("[^n", "[^"), ""]
    out += [f"[^{k[1:]}]: {v}" for k, v in NOTES.items()]
    return "\n".join(out) + "\n"


def write(path: Path, data: bytes | str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode(encoding))
    print(f"{path.relative_to(HERE)}: {path.stat().st_size} bytes")


def main() -> None:
    fb2 = fb2_book().encode("cp1251", "xmlcharrefreplace")  # ê → &#234;
    write(HERE / "ru/kapitanskaya-dochka.fb2", fb2)
    write(HERE / "ru/kapitanskaya-dochka.fb2.zip", zip_bytes([("kapitanskaya-dochka.fb2", fb2)]))
    write(HERE / "quirks/royallib-style.fb2", QUIRKS_FB2, "cp1251")
    write(HERE / "quirks/fb2-saved-as.txt", fb2)
    write(HERE / "quirks/wrapped-parts.fb2", WRAPPED_FB2)
    write(HERE / "ru/kapitanskaya-dochka.txt", txt_librus(), "cp1251")
    write(HERE / "ru/blank-lines-parts.txt", BLANK_LINES_TXT)
    write(HERE / "ru/kapitanskaya-dochka.md", md_book())
    write(HERE / "ru/kapitanskaya-dochka.epub", epub3_book())
    write(HERE / "ru/kapitanskaya-dochka-epub2.epub", epub2_book())
    write(HERE / "en/alice.epub", epub_english())
    write(HERE / "evil/xxe.fb2", XXE_FB2)
    write(HERE / "evil/billion-laughs.fb2", LAUGHS_FB2)
    write(HERE / "evil/traversal.fb2.zip", zip_bytes([("../../evil.fb2", fb2)]))
    write(HERE / "evil/zip-bomb.fb2.zip", zip_bytes([("bomb.fb2", b"\0" * (16 * 2**20))]))


if __name__ == "__main__":
    main()
