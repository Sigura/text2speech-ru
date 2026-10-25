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
    write(HERE / "evil/xxe.fb2", XXE_FB2)
    write(HERE / "evil/billion-laughs.fb2", LAUGHS_FB2)
    write(HERE / "evil/traversal.fb2.zip", zip_bytes([("../../evil.fb2", fb2)]))
    write(HERE / "evil/zip-bomb.fb2.zip", zip_bytes([("bomb.fb2", b"\0" * (16 * 2**20))]))


if __name__ == "__main__":
    main()
