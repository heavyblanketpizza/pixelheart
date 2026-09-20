"""Immutable artwork snapshots and a portable, offline frame review.

This module neither edits artwork nor assigns game behavior to extra cells.
Reports contain sanitized PNG pixels and display labels, never source paths.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from html import escape
import io
import json
from os import PathLike
import re

from PIL import Image

from .artwork import ArtworkValidationError, _path, _read_png


MAX_REVIEW_FRAMES = 512
_KINDS = ("portrait", "sprite")
_EXPRESSIONS = ("Neutral", "Happy", "Sad", "Unique", "Love", "Angry")
_DIRECTIONS = ("Down", "Right", "Up", "Left")


@dataclass(frozen=True)
class ReviewSheet:
    """A decoded PNG snapshot with a complete, proportional frame grid."""

    kind: str
    label: str
    png: bytes
    width: int
    height: int
    columns: int
    frame_width: int
    frame_height: int
    frame_count: int
    native: bool
    blank_frames: tuple[int, ...]
    warnings: tuple[str, ...]

    @property
    def display_size(self) -> tuple[int, int]:
        """Game-sized display cells, independent of source resolution."""
        return (64, 64) if self.kind == "portrait" else (16, 32)

    def frame_rect(self, index: int) -> tuple[int, int, int, int]:
        if type(index) is not int or not 0 <= index < self.frame_count:
            raise IndexError("This sheet does not contain that frame index.")
        return (
            index % self.columns * self.frame_width,
            index // self.columns * self.frame_height,
            self.frame_width,
            self.frame_height,
        )


def _check_kind(kind: str) -> None:
    if kind not in _KINDS:
        raise ArtworkValidationError("Choose portrait or sprite artwork.")


def load_review_sheet(
    path: str | PathLike[str], kind: str, label: str = "Artwork"
) -> ReviewSheet:
    """Read one safe PNG snapshot and infer its complete proportional grid.

    Incomplete *sets* of expressions or walking poses can still be reviewed;
    incomplete pixel rows cannot. PNG metadata is discarded when encoding the
    snapshot, so a report cannot accidentally disclose embedded source paths.
    """
    _check_kind(kind)
    _, decoded = _read_png(_path(path, "artwork"))
    try:
        width, height = decoded.size
        columns, minimum_width = (2, 128) if kind == "portrait" else (4, 64)
        frame_width = width // columns
        frame_height = frame_width if kind == "portrait" else frame_width * 2
        if (width < minimum_width or width % columns or not frame_height
                or height < frame_height or height % frame_height):
            shape = "square" if kind == "portrait" else "twice as tall as they are wide"
            raise ArtworkValidationError(
                f"The {kind} sheet is {width}×{height}. Use {columns} complete columns "
                f"of cells that are {shape}, with a width of at least {minimum_width} px "
                "and no partial rows. Larger proportional sheets are supported."
            )
        count = columns * (height // frame_height)
        if count > MAX_REVIEW_FRAMES:
            raise ArtworkValidationError(
                f"This sheet contains {count} frames. Review supports at most "
                f"{MAX_REVIEW_FRAMES} frames per sheet; choose a smaller sheet."
            )
        native = width == minimum_width
        notes = []
        if not native:
            dw, dh = (64, 64) if kind == "portrait" else (16, 32)
            notes.append(
                f"Source cells are {frame_width}×{frame_height}, not native {dw}×{dh}. "
                "The review displays game-sized cells with nearest-neighbor scaling; "
                "the source artwork is unchanged."
            )
        minimum_frames = 6 if kind == "portrait" else 16
        if count < minimum_frames:
            notes.append(
                f"This sheet has {count} frames; the usual minimum is {minimum_frames} "
                f"for {'portrait expressions' if kind == 'portrait' else 'four walking directions'}. "
                "Missing frames are not generated."
            )
        with decoded.convert("RGBA") as rgba:
            # A new image has no source metadata (including EXIF or text chunks).
            with Image.frombytes("RGBA", rgba.size, rgba.tobytes()) as clean:
                with clean.getchannel("A") as alpha:
                    blanks = []
                    for index in range(count):
                        x, y = index % columns * frame_width, index // columns * frame_height
                        with alpha.crop((x, y, x + frame_width, y + frame_height)) as cell:
                            if cell.getbbox() is None:
                                blanks.append(index)
                stream = io.BytesIO()
                clean.save(stream, format="PNG")
        return ReviewSheet(
            kind=kind, label=str(label), png=stream.getvalue(), width=width,
            height=height, columns=columns, frame_width=frame_width,
            frame_height=frame_height, frame_count=count, native=native,
            blank_frames=tuple(blanks), warnings=tuple(notes),
        )
    finally:
        decoded.close()


def frame_title(kind: str, index: int) -> str:
    """Conventional early-frame labels, without character-specific extra poses."""
    _check_kind(kind)
    if type(index) is not int or index < 0:
        raise IndexError("Choose a nonnegative frame index.")
    if kind == "portrait":
        name = _EXPRESSIONS[index] if index < len(_EXPRESSIONS) else "Expression"
        return f"{name} · ${index}"
    name = f"Walk {_DIRECTIONS[index // 4].lower()}" if index < 16 else "Extra pose"
    return f"{name} · {index}"


def _json_script(value: object) -> str:
    # JSON is script data, not HTML. Escaping '<' also neutralizes '</script>'.
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _sheet_data(sheet: ReviewSheet) -> dict:
    return {
        "kind": sheet.kind, "label": sheet.label,
        "png": "data:image/png;base64," + base64.b64encode(sheet.png).decode("ascii"),
        "width": sheet.width, "height": sheet.height, "columns": sheet.columns,
        "frameWidth": sheet.frame_width, "frameHeight": sheet.frame_height,
        "frameCount": sheet.frame_count, "displaySize": sheet.display_size,
        "native": sheet.native, "blankFrames": sheet.blank_frames,
        "warnings": sheet.warnings,
    }


def render_artwork_review_html(
    title: str,
    sheets: dict[str, ReviewSheet],
    references: dict[str, ReviewSheet] | None = None,
    appearance: str = "Default",
) -> str:
    """Return a self-contained review of snapshots, with optional comparisons.

    Each kind is indexed through the longer sheet; missing and fully transparent
    cells are distinct. The report does not read files or use network resources.
    ``appearance`` is a display label (for example, Default or Winter).
    """
    references = {} if references is None else references
    for group in (sheets, references):
        if not isinstance(group, dict):
            raise ArtworkValidationError("Review artwork must map portrait or sprite to a loaded sheet.")
        for kind, sheet in group.items():
            _check_kind(kind)
            if not isinstance(sheet, ReviewSheet) or sheet.kind != kind:
                raise ArtworkValidationError("Each review entry must be a loaded sheet of the matching kind.")
    kinds = [kind for kind in _KINDS if kind in sheets or kind in references]
    if not kinds:
        raise ArtworkValidationError("Add a portrait or sprite sheet to review.")
    payload = {}
    tabs, panels = [], []
    for position, kind in enumerate(kinds):
        current, reference = sheets.get(kind), references.get(kind)
        comparison = reference is not None
        sides = [("current", current)] + ([("reference", reference)] if comparison else [])
        count = max(sheet.frame_count for _, sheet in sides if sheet is not None)
        display_width, display_height = (64, 64) if kind == "portrait" else (16, 32)
        title_kind = "Portraits" if kind == "portrait" else "Sprites"
        selected = position == 0
        tabs.append(
            f'<button type="button" role="tab" id="tab-{kind}" aria-controls="panel-{kind}" '
            f'aria-selected="{str(selected).lower()}" tabindex="{0 if selected else -1}" '
            f'data-kind="{kind}">{title_kind}</button>'
        )
        summaries = []
        for side, sheet in sides:
            if sheet is None:
                summaries.append('<p class="sheet-summary">Artwork: no sheet supplied.</p>')
                continue
            key = f"{kind}-{side}"
            payload[key] = _sheet_data(sheet)
            summaries.append(
                f'<p class="sheet-summary"><strong>{escape(sheet.label)}</strong> · '
                f'{sheet.width} × {sheet.height} sheet · {sheet.frame_count} cells · '
                f'{sheet.frame_width} × {sheet.frame_height} source pixels per cell</p>'
            )
            for warning in sheet.warnings:
                summaries.append(f'<p class="warning">{escape(sheet.label)}: {escape(warning)}</p>')

        def figure(side: str, sheet: ReviewSheet | None, index: int, *, walking=False) -> str:
            label = sheet.label if sheet else "Artwork"
            missing = sheet is None or index >= sheet.frame_count or (walking and index + 3 >= sheet.frame_count)
            blank = not missing and index in sheet.blank_frames
            if missing:
                visual = '<span class="missing">Missing walking row</span>' if walking else '<span class="missing">Missing frame</span>'
                detail = "No complete four-frame row" if walking else "No source cell at this index"
            else:
                key = f"{kind}-{side}"
                motion = f' data-walk-start="{index}"' if walking else ""
                visual = (
                    f'<canvas width="{display_width}" height="{display_height}" data-sheet="{key}" '
                    f'data-frame="{index}"{motion} role="img" '
                    f'aria-label="{escape(label, quote=True)}, {escape(frame_title(kind, index), quote=True)}"></canvas>'
                    f'<span class="empty"{ "" if blank else " hidden"}>Fully transparent</span>'
                )
                detail = f"{sheet.frame_width} × {sheet.frame_height} source px"
            return (
                f'<figure><figcaption>{escape(label)}</figcaption><div class="stage">{visual}</div>'
                f'<p class="dimensions">{detail}</p></figure>'
            )

        cards = []
        for index in range(count):
            figures = "".join(figure(side, sheet, index) for side, sheet in sides)
            cards.append(
                f'<article class="frame-card" id="{kind}-frame-{index}" tabindex="-1" data-index="{index}">'
                f'<h3><span class="index">{index:02d}</span> {escape(frame_title(kind, index))}</h3>'
                f'<div class="pair{ " paired" if comparison else ""}">{figures}</div></article>'
            )
        walking_section = ""
        if kind == "sprite":
            walking_cards = []
            for direction, direction_name in enumerate(_DIRECTIONS):
                figures = "".join(figure(side, sheet, direction * 4, walking=True) for side, sheet in sides)
                walking_cards.append(
                    f'<article class="walk-card"><h3>{direction_name} · {direction * 4}–{direction * 4 + 3}</h3>'
                    f'<div class="pair{ " paired" if comparison else ""}">{figures}</div></article>'
                )
            walking_section = (
                '<section class="walking" aria-labelledby="walking-title"><div class="walking-heading">'
                '<h2 id="walking-title">Standard walking rows</h2>'
                '<button type="button" id="play" aria-pressed="false">Play walking</button></div>'
                '<p class="muted">Frames 0–15 only. Playback illustrates the artwork; game behavior is not tested.</p>'
                f'<div class="walk-grid">{"".join(walking_cards)}</div></section>'
            )
        panels.append(
            f'<section role="tabpanel" id="panel-{kind}" aria-labelledby="tab-{kind}" '
            f'data-kind="{kind}" data-count="{count}" data-columns="{2 if comparison else 1}" '
            f'style="--cell-width:{display_width}px;--cell-height:{display_height}px"'
            f'{"" if selected else " hidden"}>'
            f'<h2>{title_kind}</h2>{"".join(summaries)}{walking_section}'
            f'<p class="index-note">{count} indexed cells · zero-based indices, left to right then down · '
            f'{display_width} × {display_height} native display pixels</p>'
            f'<div class="frame-grid">{"".join(cards)}</div></section>'
        )
    replacements = {
        "TITLE": escape(str(title)), "APPEARANCE": escape(str(appearance)),
        "TABS": "".join(tabs), "PANELS": "".join(panels), "DATA": _json_script(payload),
    }
    # Substitute once; user labels resembling a template token stay literal.
    return re.sub(r"__(TITLE|APPEARANCE|TABS|PANELS|DATA)__",
                  lambda match: replacements[match.group(1)], _HTML)


_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>__TITLE__ · Artwork review</title>
<style>
:root{color-scheme:dark;--bg:#142029;--panel:#1d303b;--line:#3c5360;--text:#edf2ed;--muted:#b4c5ca;--accent:#bfe0c2;--zoom:2;font:15px/1.5 system-ui,sans-serif}
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--text)}main{max-width:1560px;margin:auto;padding:30px 24px 60px}h1{font:500 clamp(28px,5vw,44px)/1.15 Georgia,serif;margin:7px 0 16px}h2{font-size:22px;margin:20px 0 8px}h3{font-size:14px;margin:0;padding:12px 14px}p{margin:8px 0}.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:.16em;color:var(--accent)}.intro{max-width:850px}.muted,.sheet-summary,.index-note{color:var(--muted)}button,select,input{font:inherit;color:var(--text);background:#25404c;border:1px solid #627881;border-radius:6px;padding:7px 10px}button,select{cursor:pointer}button:hover{border-color:var(--accent)}button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:4px}button[aria-selected=true],button[aria-pressed=true]{background:#365b53;border-color:var(--accent)}.tabs{display:flex;gap:8px;margin-top:24px}.toolbar{display:flex;gap:18px;align-items:center;flex-wrap:wrap;border:1px solid var(--line);border-radius:10px;padding:14px;margin:16px 0}.control{display:flex;gap:8px;align-items:center}.control label{font-size:13px;color:var(--muted)}input{width:80px}#status{color:var(--muted);font-size:13px}.warning{border-left:3px solid #dec181;background:#353d38;padding:10px 14px;color:#f0e4c8}.frame-grid,.walk-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,var(--card-width,300px)),1fr));gap:16px}.frame-card,.walk-card{border:1px solid var(--line);border-radius:10px;background:var(--panel);overflow:hidden;scroll-margin-top:18px}.frame-card:focus{outline:2px solid var(--accent);outline-offset:3px}.index{font:600 20px ui-monospace,monospace;margin-right:7px;color:var(--accent)}.pair{display:grid;grid-template-columns:minmax(0,1fr)}.pair.paired{grid-template-columns:repeat(2,minmax(0,1fr))}.pair figure+figure{border-left:1px solid var(--line)}figure{margin:0;min-width:0}figcaption{font-size:12px;font-weight:600;text-align:center;padding:8px;overflow-wrap:anywhere}.stage{display:flex;align-items:center;justify-content:center;position:relative;min-height:calc(var(--cell-height) * var(--zoom) + 30px);overflow:auto;padding:12px;background-color:#233a44;background-image:conic-gradient(#2d4650 25%,transparent 0 50%,#2d4650 0 75%,transparent 0);background-size:16px 16px}.light .stage{background:#edf0e8;color:#263b44}.dark .stage{background:#0b1720}.stage canvas{display:block;flex-shrink:0;width:calc(var(--cell-width) * var(--zoom));height:calc(var(--cell-height) * var(--zoom));image-rendering:pixelated;image-rendering:crisp-edges}.dimensions{font:11px ui-monospace,monospace;text-align:center;padding:4px 8px;color:var(--muted)}.empty{position:absolute;bottom:1px;left:0;right:0;text-align:center;font-size:11px;background:#15242b;color:#e5ece8}.missing{text-align:center;font-size:13px;max-width:150px}.walking{margin:24px 0 30px}.walking-heading{display:flex;align-items:center;gap:20px;flex-wrap:wrap}.walking-heading h2{margin:0}.walking .muted{font-size:13px;margin-bottom:12px}.footer{border-top:1px solid var(--line);margin-top:32px;padding-top:16px;color:var(--muted);font-size:13px}
@media(max-width:560px){main{padding:22px 12px 40px}.toolbar{gap:12px}.control{flex-wrap:wrap}.stage{justify-content:flex-start}.stage canvas{margin:auto}h3{font-size:12px}}
</style>
</head>
<body><main>
<div class="eyebrow">PixelHeart · local artwork review</div>
<h1>__TITLE__</h1>
<p class="intro muted">Appearance: <strong>__APPEARANCE__</strong>. This report contains a snapshot of the selected artwork. Source files are unchanged. All images are embedded, so the report works offline.</p>
<div class="tabs" role="tablist" aria-label="Artwork kind">__TABS__</div>
<div class="toolbar" aria-label="Frame review controls">
<div class="control"><label for="zoom">Pixel scale</label><select id="zoom"><option value="1">Native 1×</option><option value="2" selected>2×</option><option value="4">4×</option><option value="6">6×</option><option value="8">8×</option></select></div>
<div class="control"><label for="background">Background</label><select id="background"><option value="checker">Checkerboard</option><option value="light">Light</option><option value="dark">Dark</option></select></div>
<form class="control" id="jump-form"><label for="jump">Frame index</label><input id="jump" type="number" min="0" step="1" value="0" required><button type="submit">Go</button></form>
</div>
<p id="status" role="status">Loading embedded artwork…</p>
__PANELS__
<p class="footer">Labels describe conventional early-frame slots. Extra sprite cells have no assumed action; they may contain poses, props, or empty space. Fully transparent cells are detected from their actual alpha channel. This review does not validate game animation or installation.</p>
<noscript>Enable JavaScript to draw the embedded frame images and use the review controls.</noscript>
</main>
<script id="review-data" type="application/json">__DATA__</script>
<script>
'use strict';
const data=JSON.parse(document.getElementById('review-data').textContent);
const images=new Map(), status=document.getElementById('status');
const tabs=Array.from(document.querySelectorAll('[role=tab]'));
const panels=Array.from(document.querySelectorAll('[role=tabpanel]'));
let loaded=false,playing=false,walkFrame=0,lastStep=0;
function activePanel(){return panels.find(panel=>!panel.hidden);}
function resizeCards(){
  const zoom=Number(document.getElementById('zoom').value);
  document.documentElement.style.setProperty('--zoom',zoom);
  for(const panel of panels){const width=panel.dataset.kind==='portrait'?64:16;
    panel.style.setProperty('--card-width',Math.max(260,(width*zoom+30)*Number(panel.dataset.columns)+4)+'px');}
}
function activate(tab){
  for(const item of tabs){const selected=item===tab;item.setAttribute('aria-selected',String(selected));item.tabIndex=selected?0:-1;}
  for(const panel of panels)panel.hidden=panel.dataset.kind!==tab.dataset.kind;
  const jump=document.getElementById('jump');jump.max=String(Number(activePanel().dataset.count)-1);jump.value='0';
  if(loaded)status.textContent=activePanel().dataset.count+' indexed cells. Pixel scale uses native game cell dimensions.';
}
function draw(canvas,index){
  const sheet=data[canvas.dataset.sheet], ctx=canvas.getContext('2d');
  ctx.imageSmoothingEnabled=false;ctx.clearRect(0,0,canvas.width,canvas.height);
  ctx.drawImage(images.get(canvas.dataset.sheet),index%sheet.columns*sheet.frameWidth,
    Math.floor(index/sheet.columns)*sheet.frameHeight,sheet.frameWidth,sheet.frameHeight,0,0,canvas.width,canvas.height);
  canvas.nextElementSibling.hidden=!sheet.blankFrames.includes(index);
  if(canvas.hasAttribute('data-walk-start'))canvas.setAttribute('aria-label',sheet.label+', walking preview frame '+index);
}
for(const tab of tabs){tab.addEventListener('click',()=>activate(tab));tab.addEventListener('keydown',event=>{
  const offset=event.key==='ArrowRight'?1:event.key==='ArrowLeft'?-1:0;
  if(offset){event.preventDefault();const next=tabs[(tabs.indexOf(tab)+offset+tabs.length)%tabs.length];activate(next);next.focus();}
});}
document.getElementById('zoom').addEventListener('change',resizeCards);
document.getElementById('background').addEventListener('change',event=>{document.body.className=event.target.value;});
document.getElementById('jump-form').addEventListener('submit',event=>{
  event.preventDefault();const index=Number(document.getElementById('jump').value),panel=activePanel();
  if(!Number.isInteger(index)||index<0||index>=Number(panel.dataset.count))return;
  const card=document.getElementById(panel.dataset.kind+'-frame-'+index);card.scrollIntoView({block:'start'});card.focus({preventScroll:true});
});
const play=document.getElementById('play');
if(play)play.addEventListener('click',()=>{playing=!playing;play.textContent=playing?'Pause walking':'Play walking';play.setAttribute('aria-pressed',String(playing));lastStep=0;});
function tick(time){
  if(loaded&&playing&&!document.hidden&&activePanel().dataset.kind==='sprite'&&time-lastStep>=160){
    if(lastStep)walkFrame=(walkFrame+1)%4;lastStep=time;
    for(const canvas of document.querySelectorAll('[data-walk-start]'))draw(canvas,Number(canvas.dataset.walkStart)+walkFrame);
  }
  requestAnimationFrame(tick);
}
async function start(){
  try{
    await Promise.all(Object.entries(data).map(async([key,sheet])=>{const image=new Image();image.src=sheet.png;await image.decode();
      if(image.naturalWidth!==sheet.width||image.naturalHeight!==sheet.height)throw new Error('Unexpected embedded image size');images.set(key,image);}));
    for(const canvas of document.querySelectorAll('canvas[data-sheet]'))draw(canvas,Number(canvas.dataset.frame));
    loaded=true;activate(tabs[0]);requestAnimationFrame(tick);
  }catch(error){status.textContent='The embedded artwork could not be displayed. Re-export the review from PixelHeart.';if(play)play.disabled=true;}
}
resizeCards();activate(tabs[0]);start();
</script>
</body></html>
'''
