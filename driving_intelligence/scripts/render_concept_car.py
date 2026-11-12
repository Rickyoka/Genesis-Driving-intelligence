"""Generate app/static/img/concept-gt.svg — an ORIGINAL concept GT (not any real model).

Deliberately avoids real brand cues (no crest grille, no split twin-line lamps, no badges).
Run: python scripts/render_concept_car.py
"""
import math
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "img" / "concept-gt.svg"
RW, FW, WY, TR, RR = 318, 902, 342, 80, 63   # rear/front wheel x, wheel y, tyre r, rim r


def wheel(cx, cy, uid):
    spokes = []
    for k in range(5):
        a = math.radians(k * 72 - 90)
        # five twin-spokes
        for off in (-7.5, 7.5):
            ox, oy = off * math.cos(a + math.pi / 2), off * math.sin(a + math.pi / 2)
            x1, y1 = cx + 16 * math.cos(a) + ox, cy + 16 * math.sin(a) + oy
            x2, y2 = cx + (RR - 4) * math.cos(a) + ox * 1.6, cy + (RR - 4) * math.sin(a) + oy * 1.6
            spokes.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="url(#spoke)" stroke-width="8" stroke-linecap="round"/>')
    return f'''
  <g id="{uid}">
    <circle cx="{cx}" cy="{cy}" r="{TR}" fill="url(#tyre)"/>
    <circle cx="{cx}" cy="{cy}" r="{TR - 3}" fill="none" stroke="#2a2a2d" stroke-width="1.5"/>
    <circle cx="{cx}" cy="{cy}" r="{RR + 2}" fill="#101113"/>
    <circle cx="{cx}" cy="{cy}" r="{RR}" fill="url(#barrel)"/>
    <circle cx="{cx}" cy="{cy}" r="{RR - 10}" fill="#2c2f34"/>
    <circle cx="{cx}" cy="{cy}" r="{RR - 14}" fill="none" stroke="#565b63" stroke-width="2" stroke-dasharray="3 4"/>
    <path d="M{cx - 30} {cy - 30} A42 42 0 0 1 {cx + 30} {cy - 30} L{cx + 22} {cy - 20} A30 30 0 0 0 {cx - 22} {cy - 20} Z" fill="url(#caliper)"/>
    {''.join(spokes)}
    <circle cx="{cx}" cy="{cy}" r="{RR}" fill="none" stroke="url(#lip)" stroke-width="5"/>
    <circle cx="{cx}" cy="{cy}" r="15" fill="url(#hub)"/>
    <circle cx="{cx}" cy="{cy}" r="5" fill="#1a1b1e"/>
    <circle cx="{cx}" cy="{cy}" r="{RR}" fill="url(#rimshine)"/>
  </g>'''


BODY = (f"M150 334 C140 322 136 300 140 286 C142 270 148 258 160 250 "
        f"L196 236 C260 214 380 168 470 152 C540 140 610 140 668 156 "
        f"C720 170 772 200 812 226 C880 236 960 246 1010 258 "
        f"C1040 266 1060 282 1064 300 C1067 314 1064 326 1052 336 "
        f"L1000 338 A{TR + 14} {TR + 14} 0 0 0 {FW - (TR + 14) + (FW - 900)} 338 "
        f"L{RW + TR + 14} 340 A{TR + 14} {TR + 14} 0 0 0 {RW - TR - 14} 338 Z")

GLASS = ("M232 244 C300 218 400 182 478 168 C546 156 612 158 662 172 "
         "C700 184 736 204 762 222 C700 226 560 236 430 244 C340 250 270 252 232 244 Z")

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 520" role="img" aria-label="오리지널 컨셉 GT 렌더링 (실존 차량 아님)">
  <title>Original concept GT — illustrative rendering, not a real production model</title>
  <defs>
    <linearGradient id="paint" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#8d96a3"/><stop offset=".18" stop-color="#4a525d"/>
      <stop offset=".42" stop-color="#262b33"/><stop offset=".62" stop-color="#3a414b"/>
      <stop offset=".78" stop-color="#171a1f"/><stop offset="1" stop-color="#0a0b0d"/>
    </linearGradient>
    <linearGradient id="paintH" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#000" stop-opacity=".55"/><stop offset=".25" stop-color="#000" stop-opacity="0"/>
      <stop offset=".7" stop-color="#fff" stop-opacity=".06"/><stop offset="1" stop-color="#000" stop-opacity=".5"/>
    </linearGradient>
    <linearGradient id="glass" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2b3340"/><stop offset=".6" stop-color="#0b0e13"/><stop offset="1" stop-color="#050608"/>
    </linearGradient>
    <linearGradient id="streak" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".5" stop-color="#e8f1ff" stop-opacity=".28"/><stop offset="1" stop-color="#fff" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="shoulder" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".35" stop-color="#fff" stop-opacity=".55"/>
      <stop offset=".8" stop-color="#fff" stop-opacity=".25"/><stop offset="1" stop-color="#fff" stop-opacity="0"/>
    </linearGradient>
    <radialGradient id="tyre" cx=".5" cy=".45" r=".55"><stop offset=".7" stop-color="#1b1b1d"/><stop offset="1" stop-color="#050505"/></radialGradient>
    <radialGradient id="barrel" cx=".5" cy=".5" r=".5"><stop offset=".6" stop-color="#3a3e45"/><stop offset="1" stop-color="#15171a"/></radialGradient>
    <linearGradient id="spoke" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#9aa1aa"/><stop offset=".45" stop-color="#4c525a"/><stop offset="1" stop-color="#25282d"/></linearGradient>
    <linearGradient id="lip" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#c3c8cf"/><stop offset=".5" stop-color="#555b63"/><stop offset="1" stop-color="#1f2125"/></linearGradient>
    <radialGradient id="hub" cx=".4" cy=".35" r=".7"><stop offset="0" stop-color="#cfd3d9"/><stop offset="1" stop-color="#4b5058"/></radialGradient>
    <radialGradient id="rimshine" cx=".35" cy=".25" r=".6"><stop offset="0" stop-color="#fff" stop-opacity=".22"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
    <linearGradient id="caliper" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff8a4c"/><stop offset="1" stop-color="#c94a12"/></linearGradient>
    <radialGradient id="shadow" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#000" stop-opacity=".85"/><stop offset=".7" stop-color="#000" stop-opacity=".35"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>
    <linearGradient id="fade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".5"/><stop offset=".8" stop-color="#fff" stop-opacity="0"/></linearGradient>
    <mask id="reflMask"><rect x="0" y="{WY + TR}" width="1200" height="100" fill="url(#fade)"/></mask>
    <filter id="blur6"><feGaussianBlur stdDeviation="6"/></filter>
    <filter id="glow"><feGaussianBlur stdDeviation="3.5" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
    <clipPath id="glassClip"><path d="{GLASS}"/></clipPath>
    <clipPath id="bodyClip"><path d="{BODY}"/></clipPath>
  </defs>

  <ellipse cx="610" cy="{WY + TR - 2}" rx="520" ry="22" fill="url(#shadow)" filter="url(#blur6)"/>

  <g id="car">
    <path d="{BODY}" fill="url(#paint)"/>
    <path d="{BODY}" fill="url(#paintH)"/>
    <g clip-path="url(#bodyClip)">
      <path d="M170 262 C420 236 760 230 1040 270 L1040 276 C760 238 420 244 170 270 Z" fill="url(#shoulder)" opacity=".7"/>
      <rect x="120" y="300" width="960" height="60" fill="#06070a" opacity=".55"/>
      <path d="M380 318 C560 312 760 312 820 316" stroke="#9aa3ae" stroke-opacity=".25" stroke-width="1.5" fill="none"/>
    </g>
    <path d="{GLASS}" fill="url(#glass)"/>
    <g clip-path="url(#glassClip)">
      <polygon points="430,140 520,140 360,260 270,260" fill="url(#streak)"/>
      <polygon points="600,140 640,140 520,260 480,260" fill="url(#streak)" opacity=".6"/>
    </g>
    <path d="{GLASS}" fill="none" stroke="#9aa3ae" stroke-opacity=".35" stroke-width="2"/>
    <path d="M520 166 C516 196 512 222 510 240" stroke="#0a0b0d" stroke-width="7" fill="none"/>
    <path d="M512 252 C508 280 506 300 506 322" stroke="#05060a" stroke-opacity=".7" stroke-width="1.6" fill="none"/>
    <path d="M790 238 C792 270 796 300 800 322" stroke="#05060a" stroke-opacity=".6" stroke-width="1.4" fill="none"/>
    <rect x="600" y="262" width="56" height="7" rx="3.5" fill="#0d0f12" stroke="#7d858f" stroke-opacity=".5"/>
    <path d="M752 222 C760 210 786 208 798 216 C803 226 794 234 778 234 C764 234 754 230 752 222 Z" fill="#2e343c" stroke="#000" stroke-opacity=".5"/>
    <path d="M756 220 C766 212 784 212 794 217" stroke="#c9d0d8" stroke-opacity=".35" stroke-width="1.5" fill="none"/>
    <path d="M200 238 C300 202 420 162 500 150 C570 140 630 144 670 156" stroke="#fff" stroke-opacity=".18" stroke-width="3" fill="none"/>
    <path d="M1004 268 C1024 271 1044 278 1056 290" stroke="#f4f8ff" stroke-width="4" stroke-linecap="round" fill="none" filter="url(#glow)"/>
    <path d="M140 270 C146 262 156 256 168 252" stroke="#ff2a3a" stroke-width="4.5" stroke-linecap="round" fill="none" filter="url(#glow)"/>
    <path d="M1000 336 L1060 330 L1062 338 L1004 342 Z" fill="#050608"/>
    <path d="M146 334 L210 340 L208 346 L150 342 Z" fill="#050608"/>
    {wheel(RW, WY, "wr")}
    {wheel(FW, WY, "wf")}
  </g>

  <g mask="url(#reflMask)" opacity=".45">
    <use href="#car" transform="translate(0 {2 * (WY + TR)}) scale(1 -1)"/>
  </g>
</svg>
'''
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(svg, encoding="utf-8")
print("wrote", OUT, len(svg))
