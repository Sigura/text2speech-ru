"""Covers (§6.8.2, FR-23, ADR-0009): typographic renderer, prompts, the cover choice in
`convert`, and the `sd-webui` generator against a fake WebUI on loopback."""

from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from text2audio.core import resources as res
from text2audio.core.config import load_config
from text2audio.core.convert import convert
from text2audio.covers import prompt, typographic
from text2audio.models.store import ModelStore

FIXTURES = Path(__file__).parent / "fixtures" / "ru"
TITLE = "fake.safetensors [0123456789]"

FAKE_WEBUI = r"""
import base64, io, json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from PIL import Image

args = sys.argv[1:]
if os.environ.get("FAKE_SD_CRASH"):
    sys.exit(3)
port = int(args[args.index("--port") + 1])
data = args[args.index("--data-dir") + 1]
title = json.load(open(os.path.join(data, "config.json")))["sd_model_checkpoint"]
log = os.environ["FAKE_SD_LOG"]
keys = ("CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER", "HF_HUB_OFFLINE", "SD_WEBUI_CACHE_DIR")
with open(log, "w") as f:
    json.dump({"argv": args, "cwd": os.getcwd(), "pid": os.getpid(),
               "env": {k: os.environ.get(k) for k in keys}, "title": title}, f)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, body):
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/sdapi/v1/sd-models":
            self.reply([{"title": title, "model_name": "fake", "sha256": None}])
        elif self.path == "/sdapi/v1/options":
            self.reply({"sd_model_checkpoint": title})
        else:
            self.send_error(404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        body.pop("init_images", None)
        with open(log + ".txt2img", "a") as f:
            f.write(json.dumps({"path": self.path, **body}) + "\n")
        png = io.BytesIO()
        Image.new("RGB", (64, 48), (120, 30, 200)).save(png, "PNG")
        self.reply({"images": [base64.b64encode(png.getvalue()).decode()]})


HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


# --------------------------------------------------------------------------- typographic


def test_typographic_cover_is_square_and_deterministic(tmp_path: Path) -> None:
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    typographic.save(typographic.render("Капитанская дочка", "Александр Пушкин"), a)
    typographic.save(typographic.render("Капитанская дочка", "Александр Пушкин"), b)
    assert a.read_bytes() == b.read_bytes()
    with Image.open(a) as img:
        assert img.size == (typographic.SIZE, typographic.SIZE) and img.mode == "RGB"
    other = typographic.render("Дубровский", "Александр Пушкин")
    with Image.open(a) as img:
        assert other.tobytes() != img.tobytes()


def test_long_title_fits_inside_the_frame() -> None:
    title = "Алмазный век, или Букварь для благородных девиц " * 3
    img = typographic.render(title, "Нил Стивенсон")
    margin = typographic.SIZE // 18
    border = img.crop((0, 0, margin - 2, typographic.SIZE))  # left of the frame
    assert border.getcolors(40) is not None  # only the background gradient, no text


def test_overlay_crops_to_a_square_cover() -> None:
    png = io.BytesIO()
    Image.new("RGB", (300, 200), (10, 120, 30)).save(png, "PNG")
    img = typographic.overlay(png.getvalue(), "Капитанская дочка", "Пушкин")
    assert img.size == (typographic.SIZE, typographic.SIZE)
    assert img.getpixel((10, 10)) == (10, 120, 30)  # the picture, above the title band


# --------------------------------------------------------------------------- prompts


def test_genre_keywords_from_fb2_codes_and_epub_subjects() -> None:
    assert prompt.keywords(["sf_space"]) == [prompt.GENRES["sf_space"]]
    assert prompt.keywords(["det_police", "Science Fiction"])[0].startswith("mystery")
    assert prompt.keywords(["Fiction / Science Fiction / Space Opera"])[0].startswith("science")
    assert prompt.keywords(["unknown"]) == []


def test_prompt_is_english_only_without_translation() -> None:
    text, negative = prompt.build(["prose_rus_classic"], "1836", None, None)
    assert "19th century" in text and "russian classic" in text
    assert not any("а" <= ch <= "я" for ch in text.lower())
    assert "text" in negative and "watermark" in negative
    text, _ = prompt.build([], None, "The Captain's Daughter.", "A young officer.")
    assert '"The Captain\'s Daughter"' in text and "A young officer." in text


def test_blurb_keeps_whole_sentences() -> None:
    text = "Первое предложение. " + "Второе очень длинное предложение. " * 20
    out = prompt.blurb(text, 60)
    assert out.startswith("Первое предложение.") and len(out) <= 60 and out.endswith(".")


# --------------------------------------------------------------------------- choice in convert


def _config(tmp_path: Path, **cover: Any) -> Any:
    return load_config([], {"cover": cover})


def _meta(folder: Path | None) -> dict[str, Any]:
    assert folder is not None
    return dict(json.loads((folder / "_work" / "metadata.json").read_text("utf-8")))


def test_book_without_cover_gets_a_typographic_one(tmp_path: Path) -> None:
    store = ModelStore(tmp_path / "store")
    [r] = convert(
        [FIXTURES / "kapitanskaya-dochka.txt"],
        tmp_path / "out",
        config=_config(tmp_path),
        store=store,
    )
    cover = _meta(r.folder)["cover"]
    assert cover["origin"] == "typographic" and "[cover.sd] dir" in cover["note"]
    assert r.folder is not None
    path = r.folder / "_work" / "cover.jpg"
    stamp = path.stat().st_mtime_ns
    [again] = convert(
        [FIXTURES / "kapitanskaya-dochka.txt"],
        tmp_path / "out",
        config=_config(tmp_path),
        store=store,
    )
    assert again.folder == r.folder and path.stat().st_mtime_ns == stamp  # cached


@pytest.fixture
def real_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """The FB2 fixture's embedded cover (4 × 4 px) counts as a picture, not a placeholder."""
    from text2audio.covers import make

    monkeypatch.setattr(make, "PLACEHOLDER_PX", 1)


def test_placeholder_embedded_cover_is_replaced(tmp_path: Path) -> None:
    [r] = convert(
        [FIXTURES / "kapitanskaya-dochka.fb2"], tmp_path / "out", config=_config(tmp_path)
    )
    assert _meta(r.folder)["cover"]["origin"] == "typographic"
    assert r.folder is not None and [p.name for p in (r.folder / "_work").glob("cover*")] == [
        "cover.jpg"
    ]


def test_embedded_cover_wins_unless_mode_says_otherwise(tmp_path: Path, real_size: None) -> None:
    store = ModelStore(tmp_path / "store")
    fb2 = FIXTURES / "kapitanskaya-dochka.fb2"
    [r] = convert([fb2], tmp_path / "out", config=_config(tmp_path), store=store)
    assert _meta(r.folder)["cover"]["origin"] == "embedded"
    [r] = convert(
        [fb2], tmp_path / "out", config=_config(tmp_path, mode="typographic"), store=store
    )
    assert _meta(r.folder)["cover"]["origin"] == "typographic"
    assert r.folder is not None and [p.name for p in (r.folder / "_work").glob("cover.*")] == [
        "cover.jpg"
    ]
    [r] = convert([fb2], tmp_path / "out", config=_config(tmp_path), store=store)
    assert _meta(r.folder)["cover"]["origin"] == "embedded"


def test_embedded_only_leaves_a_book_without_cover(tmp_path: Path) -> None:
    store = ModelStore(tmp_path / "store")
    txt = FIXTURES / "kapitanskaya-dochka.txt"
    convert([txt], tmp_path / "out", config=_config(tmp_path), store=store)
    [r] = convert(
        [txt], tmp_path / "out", config=_config(tmp_path, mode="embedded-only"), store=store
    )
    assert _meta(r.folder)["cover"] is None
    assert r.folder is not None and not list((r.folder / "_work").glob("cover.*"))


# --------------------------------------------------------------------------- sd-webui


@pytest.fixture
def webui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake WebUI checkout with one SD 1.x checkpoint, one free GPU, a private data dir."""
    root = tmp_path / "webui"
    (root / "models" / "Stable-diffusion").mkdir(parents=True)
    (root / "launch.py").write_text(FAKE_WEBUI, "utf-8")
    (root / "config.json").write_text(json.dumps({"sd_model_checkpoint": TITLE}), "utf-8")
    header = json.dumps({"model.diffusion_model.input_blocks.0.0.weight": {}}).encode()
    ckpt = root / "models" / "Stable-diffusion" / "fake.safetensors"
    ckpt.write_bytes(struct.pack("<Q", len(header)) + header + b"\0" * 64)
    gpu = res.Gpu(index=0, uuid="GPU-fake", name="Fake", total_mb=24000, free_mb=20000)
    monkeypatch.setattr(
        res, "probe", lambda *a, **k: res.Resources(frozenset({0}), None, 8000, 8000, (gpu,))
    )
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("FAKE_SD_LOG", str(tmp_path / "webui.json"))
    return root


def _sd(root: Path, **extra: Any) -> dict[str, Any]:
    return {"sd": {"dir": str(root), "python": sys.executable, "timeout_s": 60, **extra}}


def _gone(pid: int) -> bool:
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1"])
def test_sd_webui_generates_on_its_own_private_instance(tmp_path: Path, webui: Path) -> None:
    store = ModelStore(tmp_path / "store")  # no Opus-MT files: the prompt is not translated
    config = load_config([], {"cover": _sd(webui)})
    [r] = convert(
        [FIXTURES / "kapitanskaya-dochka.txt"], tmp_path / "out", config=config, store=store
    )
    cover = _meta(r.folder)["cover"]
    assert cover["origin"] == "generated" and cover["generator"] == "sd-webui"
    ckpt = webui / "models" / "Stable-diffusion" / "fake.safetensors"
    assert cover["checkpoint"] == "fake"
    assert cover["checkpoint_sha256"] == hashlib.sha256(ckpt.read_bytes()).hexdigest()
    assert cover["size"] == 1024 and cover["steps"] == 30 and not cover["translated"]
    assert any("not translated" in w for w in r.warnings)
    log = json.loads((tmp_path / "webui.json").read_text("utf-8"))
    argv = log["argv"]
    private = tmp_path / "data" / "text2audio" / "sd-webui"
    assert argv[argv.index("--data-dir") + 1] == str(private) == log["cwd"]
    assert argv[argv.index("--models-dir") + 1] == str(webui / "models")
    assert "--nowebui" in argv and "--listen" not in argv and "--disable-all-extensions" in argv
    assert log["env"] == {
        "CUDA_VISIBLE_DEVICES": "0",
        "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
        "HF_HUB_OFFLINE": "1",
        "SD_WEBUI_CACHE_DIR": str(private / "cache"),
    }
    assert log["title"] == TITLE
    [call] = [
        json.loads(x) for x in (tmp_path / "webui.json.txt2img").read_text("utf-8").splitlines()
    ]
    assert call["seed"] == cover["seed"] and call["prompt"] == cover["prompt"]
    assert call["enable_hr"] and call["width"] == 512 and not call["save_images"]
    assert _gone(log["pid"])  # the instance is stopped
    assert json.loads((webui / "config.json").read_text("utf-8")) == {"sd_model_checkpoint": TITLE}
    (tmp_path / "webui.json").unlink()
    convert([FIXTURES / "kapitanskaya-dochka.txt"], tmp_path / "out", config=config, store=store)
    assert not (tmp_path / "webui.json").exists()  # cached: no second start


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1"])
def test_checkpoint_override_reaches_the_private_config(tmp_path: Path, webui: Path) -> None:
    other = "other.safetensors"
    src = webui / "models" / "Stable-diffusion"
    (src / other).write_bytes((src / "fake.safetensors").read_bytes())
    config = load_config([], {"cover": _sd(webui, checkpoint=other)})
    convert(
        [FIXTURES / "kapitanskaya-dochka.txt"],
        tmp_path / "out",
        config=config,
        store=ModelStore(tmp_path / "s"),
    )
    assert json.loads((tmp_path / "webui.json").read_text("utf-8"))["title"] == other


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1"])
def test_webui_failure_falls_back_to_typographic(
    tmp_path: Path, webui: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SD_CRASH", "1")
    config = load_config([], {"cover": _sd(webui)})
    [r] = convert(
        [FIXTURES / "kapitanskaya-dochka.txt"],
        tmp_path / "out",
        config=config,
        store=ModelStore(tmp_path / "s"),
    )
    assert _meta(r.folder)["cover"]["origin"] == "typographic"
    assert any("WebUI exited with code 3" in w for w in r.warnings)


def test_unavailable_reasons(tmp_path: Path, webui: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from text2audio_cover_sd_webui.generator import SdWebUI

    def why(**sd: Any) -> str | None:
        return SdWebUI().unavailable(load_config([], {"cover": {"sd": sd}}).cover)

    assert why() is not None and "[cover.sd] dir" in str(why())
    assert "launch.py" in str(why(dir=str(tmp_path)))
    assert "not found" in str(why(dir=str(webui), python=sys.executable, checkpoint="missing.ckpt"))
    assert why(dir=str(webui), python=sys.executable) is None
    monkeypatch.setattr(
        res, "probe", lambda *a, **k: res.Resources(frozenset({0}), None, 8000, 8000, ())
    )
    assert "no GPU" in str(why(dir=str(webui), python=sys.executable))


def test_title_ink_contrasts_with_the_picture() -> None:
    light = Image.new("RGB", (100, 40), (235, 230, 220))
    dark = Image.new("RGB", (100, 40), (20, 25, 40))
    ink, outline = typographic.ink_for(light)
    assert typographic.contrast(ink, (235, 230, 220)) > 7 and outline == (255, 255, 255)
    ink, outline = typographic.ink_for(dark)
    assert typographic.contrast(ink, (20, 25, 40)) > 7 and outline == (0, 0, 0)


def test_opening_words_stand_in_for_a_missing_annotation() -> None:
    md = "# Дочка\n\n<!-- t2a -->\n\n## Глава I\n\nБереги *честь* смолоду.[^1] Отец мой\n"
    assert prompt.opening(md, 5) == "Береги честь смолоду. Отец мой"


@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1"])
def test_small_embedded_cover_is_repainted_from_its_tags(
    tmp_path: Path, webui: Path, monkeypatch: pytest.MonkeyPatch, real_size: None
) -> None:
    from text2audio.covers import make

    tags = ["castle", "snow", "nude", "1girl"]
    monkeypatch.setattr(make, "_tag", lambda books, *a: {id(b): tags for b in books})
    config = load_config([], {"cover": _sd(webui)})
    fb2 = FIXTURES / "kapitanskaya-dochka.fb2"  # its embedded cover is 4 × 4 px
    store = ModelStore(tmp_path / "s")
    [r] = convert([fb2], tmp_path / "out", config=config, store=store)
    assert r.folder is not None
    cover = _meta(r.folder)["cover"]
    assert cover["origin"] == "repainted" and cover["denoise"] == 0.4
    assert cover["file"] == make.FILE and (r.folder / "_work" / cover["original"]).is_file()
    assert "castle, snow, 1girl" in cover["prompt"]  # explicit tags are dropped
    [call] = [json.loads(x) for x in (tmp_path / "webui.json.txt2img").read_text().splitlines()]
    assert call["path"] == "/sdapi/v1/img2img" and call["denoising_strength"] == 0.4
    assert (call["width"], call["height"]) == (1024, 1024) and call["seed"] == cover["seed"]
    (tmp_path / "webui.json").unlink()
    [r] = convert([fb2], tmp_path / "out", config=config, store=store)
    assert _meta(r.folder)["cover"]["origin"] == "repainted"
    assert not (tmp_path / "webui.json").exists()  # cached: no second start


def test_large_embedded_cover_is_kept(tmp_path: Path, webui: Path, real_size: None) -> None:
    config = load_config([], {"cover": {**_sd(webui), "repaint_below": 2}})
    [r] = convert([FIXTURES / "kapitanskaya-dochka.fb2"], tmp_path / "out", config=config)
    assert _meta(r.folder)["cover"]["origin"] == "embedded"
    assert not (tmp_path / "webui.json").exists()  # no WebUI start for it


# --------------------------------------------------------------------------- real models

SD_DIR = Path(os.environ.get("T2A_SD_DIR", ""))


def _translator_ready() -> bool:
    from text2audio_mt_opus.descriptor import OpusMtRuEn

    from text2audio.core.envs import plugin_env
    from text2audio.models.lock import assets_for

    env = plugin_env(OpusMtRuEn())
    assets = assets_for("mt:opus-mt-ru-en")
    return bool(env and env.installed and assets and not ModelStore().verify(assets))


@pytest.mark.models
@pytest.mark.skipif(not _translator_ready(), reason="Opus-MT env or assets missing")
def test_opus_mt_translates_offline() -> None:
    from text2audio_mt_opus.descriptor import OpusMtRuEn

    from text2audio.covers.make import _run_translator

    out = _run_translator(OpusMtRuEn(), ["Капитанская дочка"], ModelStore())
    assert "captain" in out["Капитанская дочка"].lower()


@pytest.mark.models
@pytest.mark.gpu
@pytest.mark.slow
@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.skipif(not (SD_DIR / "launch.py").is_file(), reason=f"no WebUI at {SD_DIR}")
def test_real_sd_webui_cover(tmp_path: Path) -> None:
    from text2audio_cover_sd_webui.generator import SdWebUI

    config = load_config([], {"cover": {"mode": "generate", "sd": {"dir": str(SD_DIR)}}})
    reason = SdWebUI().unavailable(config.cover)
    if reason:
        pytest.skip(reason)
    [r] = convert([FIXTURES / "kapitanskaya-dochka.fb2"], tmp_path / "out", config=config)
    cover = _meta(r.folder)["cover"]
    print(json.dumps(cover, ensure_ascii=False, indent=1), r.warnings)
    assert cover["origin"] == "generated", r.warnings
    assert len(cover["checkpoint_sha256"]) == 64 and cover["translated"]
    assert r.folder is not None
    with Image.open(r.folder / "_work" / cover["file"]) as img:
        assert img.size == (typographic.SIZE, typographic.SIZE)
    out = os.environ.get("T2A_COVER_OUT")
    if out:  # keep the image for a look
        Path(out).write_bytes((r.folder / "_work" / cover["file"]).read_bytes())


def _tagger_ready() -> bool:
    from text2audio_tagger_wd14.descriptor import Wd14Tagger

    from text2audio.core.envs import plugin_env
    from text2audio.models.lock import assets_for

    env = plugin_env(Wd14Tagger())
    assets = assets_for("tagger:wd14")
    return bool(env and env.installed and assets and not ModelStore().verify(assets))


@pytest.mark.models
@pytest.mark.skipif(not _tagger_ready(), reason="WD14 tagger env or assets missing")
def test_wd14_tags_an_image_offline(tmp_path: Path) -> None:
    from text2audio.core.workspace import BookFolder
    from text2audio.covers.make import _Book, _tag

    work = tmp_path / "book" / "_work"
    work.mkdir(parents=True)
    img = Image.new("RGB", (256, 256), (250, 250, 250))
    for x in range(40, 216):  # a dark square on white
        for y in range(40, 216):
            img.putpixel((x, y), (10, 10, 10))
    img.save(work / "cover.png")
    book = _Book(BookFolder(tmp_path / "book"), {"cover": {"file": "cover.png"}}, "k")
    tags = _tag([book], load_config([], {}), ModelStore())
    assert not book.warnings and tags[id(book)], book.warnings


@pytest.mark.models
@pytest.mark.gpu
@pytest.mark.slow
@pytest.mark.enable_socket
@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.skipif(not (SD_DIR / "launch.py").is_file(), reason=f"no WebUI at {SD_DIR}")
@pytest.mark.skipif(not _tagger_ready(), reason="WD14 tagger env or assets missing")
def test_real_repaint_of_a_small_embedded_cover(tmp_path: Path, real_size: None) -> None:
    from text2audio_cover_sd_webui.generator import SdWebUI

    config = load_config([], {"cover": {"sd": {"dir": str(SD_DIR)}}})
    reason = SdWebUI().unavailable(config.cover)
    if reason:
        pytest.skip(reason)
    [r] = convert([FIXTURES / "kapitanskaya-dochka.fb2"], tmp_path / "out", config=config)
    cover = _meta(r.folder)["cover"]
    print(json.dumps(cover, ensure_ascii=False, indent=1), r.warnings)
    assert cover["origin"] == "repainted", r.warnings
    assert r.folder is not None
    with Image.open(r.folder / "_work" / cover["file"]) as img:
        assert max(img.size) == 1024
