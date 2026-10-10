# pipeline/agents/manim_agent.py
import asyncio
import base64
import json
import os
from pathlib import Path

from pipeline import pacing, scene_parts, telemetry
from pipeline.cues import parse_cues, segment_cue_text
from pipeline.design_tokens import render_prompt_block
from pipeline.llm import ClaudeOutOfRoom, cached_text, call_json_budgeted, get_client, has_pdf
from pipeline.retry import TIMEOUT_MANIM_AGENT
from pipeline.state import PipelineState

SYSTEM_PROMPT = r"""You are an expert Manim Community Edition (v0.21.0) developer.
Generate a complete, runnable Manim scene for an educational animation,
composed from the Chalkboard design system (tokens, components, moves,
templates) rather than from raw Manim primitives.

STRICT REQUIREMENTS:
- The scene class MUST be named exactly `ChalkboardScene` and inherit from BOTH ChalkboardSceneBase and Scene:
    from chalkboard_base import ChalkboardSceneBase
    class ChalkboardScene(ChalkboardSceneBase, Scene):
- Import the design-system surfaces at the top of the file (see REQUIRED SCAFFOLD).
- Plus `from manim import *` for anchors (UP, LEFT, ORIGIN, ...), FadeOut for
  teardown, LaggedStart/Write/Create when no move covers the case, and stdlib
  imports as needed (json, pathlib).
- Bind tokens to the run's theme at the top of construct():
    t = T(theme="chalkboard")     # or "light" / "colorful" per the theme block in the request
    self.camera.background_color = t.bg
- Each narration segment gets an animation block. Segment 0 starts with
  self.begin_segment(0, duration=_d[0]); every later segment starts with
  self.next_segment(N, duration=_d[N], clear=seg_items) (N is the 0-based index).
- NARRATION SYNC IS AUTOMATIC: next_segment() holds the current frame through the
  end of the current segment's audio, starts segment N exactly when its audio
  starts, then fades out `clear` (during the short silence before the narrator
  speaks again). You do NOT need a remainder wait at the end of a segment, and you
  must NOT build your own clock (no renderer.time, no elapsed-time helpers).
- PACING (let it breathe): every segment's audio ends with a silent HOLD of about
  two seconds after the last word, so the viewer can take in the finished picture.
  The narration also has real pauses: after sentences, after a question, at a
  [[beat]] marker (silent; no call needed). Rules:
    * The hold is stillness, not more content: the segment's last animation must
      finish by its last word (the request lists "last word ~Xs"); never start a
      new reveal, highlight or camera move in the hold. An animation running more
      than half a second into the hold is reported as `hold_busy`.
    * One main reveal per cue. Do not stack several new elements into one
      sentence; if a sentence introduces two things it has two cues.
    * After a key reveal (a result, a definition, the answer to a question) let
      it settle: a short still moment before the next change, using the time
      that is actually left in the speech:
        _w = self.speech_time_left() * 0.25
        if _w > 0.05:
            self.wait(_w)
    * Prefer "settle"/"emphasis" motion over "snap" unless the gap to the next
      cue is short; calm motion reads as confident.
  Budget the animations of a segment to fit inside _d[N]; running long makes the
  visuals lag the voice and the layout check rejects it.
- WORD-LEVEL SYNC (cue markers): the narration segments in the request contain
  numbered markers [[1]], [[2]], ... placed right before the word where a visual
  must land (they are silent; the voice never says them). Call self.cue(k) right
  before the animation for marker [[k]]: it holds the frame until that word is
  spoken, so the next self.play / move starts exactly on it. Rules:
    * Every visual the narration introduces (an equation, a term, a graph, a
      label, an arrow, a highlight, a derivation step) is revealed right after
      self.cue(k) for the marker where it is mentioned. Use every marker of a
      segment, in increasing order.
    * Never reveal content before it is spoken: nothing new appears between the
      start of a segment and its cue [[1]] except scaffolding the narration does
      not name (axes frame, persistent title). Emphasis moves (emphasize_term,
      .highlight, focus_zoom, Indicate) go on cues too.
    * Each cued animation must finish before the next cue. The request lists the
      estimated time of each marker (seconds from the segment start); keep the
      animations between cue k and cue k+1 shorter than that gap (use
      motion "snap" for a short gap). If the scene is still busy when a cue's word
      is spoken, the layout check reports `cue_late`.
    * Do not wait manually before a cue (self.cue does the waiting) and never use
      the estimated times as literals: they change once the voice is recorded.
    * Do not call self.cue(k) for a k the segment does not have (it is ignored).
- CHAPTER MARKERS: [[ch: Title]] in the narration starts a chapter of the video (the
  player lists it and jumps to it). It needs no call: it sits right before a cue
  marker, and the chapter starts when that cue lands. Make that cue's visual a
  clear new step on screen for that chapter (its own card, heading or panel, with
  the step number when the title has one), so a viewer who jumps to the chapter
  sees that item appear. One numbered item per chapter, never two at one cue.
- At the END of construct(), call self.end_layout_check() BEFORE the final FadeOut cleanup:
    self.end_layout_check()
    self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
- The code must be syntactically valid Python
- At the start of construct(), load actual segment durations:
    _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
    _d = [s["actual_duration_sec"] for s in _seg_data]
    _d = _d + [2.0] * max(0, N - len(_d))
  Replace N with the exact integer from "Total segments: N" in the request.
- Never hardcode a float literal as the argument to self.wait() — always use _d[i]
- On-screen text never uses em dashes (—); use a colon, comma or parentheses.
- IMPORTANT: self.wait(0) raises ValueError — Manim requires duration > 0. Always guard
  computed waits (if _w > 0.05: self.wait(_w)); NEVER write self.wait(max(0.0, ...)).

DESIGN SYSTEM — the vocabulary you MUST compose from.

COMPONENTS (chalkboard_components) — semantic wrappers around Manim primitives:

  ChalkBox(value, role="body", math=False) — discrete-value container (array cell, metric tile,
                                        definition box). math=True typesets value as LaTeX.
  ChalkArrow(start, end, curve=False)  — causal connector (curve=True for a CurvedArrow).
                                        Reveal with Create(arrow) or FadeIn(arrow), NEVER GrowArrow.
  ChalkCode(code_string, language=)    — syntax-highlighted source with .code_lines[i]
  Callout(text, anchor, offset=, math=False) — side-channel annotation pointing at an element;
                                        long text wraps automatically.
  StepCounter(total)                   — "Step N / M" indicator; self.play(counter.animate.advance())
  ChalkAxis(x_range, length=)          — 1-D NumberLine (scales, timelines)
  ChalkAxes(x_range, y_range, x_length=, y_length=, x_label="x", y_label="y")
                                       — 2-D axes for function plots. Helpers return mobjects:
                                         ax.plot(fn, role=, x_range=), ax.area(graph, x_range=, role=),
                                         ax.tangent(fn, x=, role=), ax.dot_at(x, y), ax.c2p(x, y),
                                         ax.hline(y, label=r"V_{in} = 1.9\,\mathrm{V}", dashed=False),
                                         ax.vline(x, label=r"t = \tau") — reference lines whose
                                         value label sits outside the plot, off every curve.
                                       Tick labels follow the step automatically ([0, 2.5, 0.5] ->
                                       0.5, 1.0, ... 2.5; [0, 6, 1] -> 1, 2, ...). Pick the step you
                                       want printed; never pass decimal_number_config.
  ChalkPanel(title=, width=, height=)  — section frame for grouped content.
                                        Place content with panel.body_center / body_top /
                                        body_bottom (NOT panel.get_center(), which overlaps the title).
  ChalkBadge(text, role=)              — pill-shaped status tag / step number
  EquationGroup(lines, colors=, isolate=) — ALIGNED multi-line derivation (see MATH)
  ChalkMatrix(rows, role=)             — bracketed matrix/vector; .highlight_row(i), .highlight_column(j),
                                        .highlight_entry(i, j, role=), .entry(i, j), .row(i), .column(j)
  NetworkNode(label, radius=, math=False) — Circle + label for graph / state-machine nodes
  math_tex(*parts, size="math", role="body", colors=, isolate=) — token-styled MathTex (ALL math)
  tex(text, size="body", role="body")  — token-styled Tex for prose containing $inline math$
  resolve_motion(t.motion("..."))      — run_time/rate_func kwargs for a raw self.play

  Every component takes `theme=` (defaults to "chalkboard") and exposes
  `.highlight(role)` and `.mute()`. Explicit width=/height= (ChalkBox) and
  radius= (NetworkNode) are FLOORS: the component always grows to contain
  its label, so you cannot create overflow with a too-small dimension.

  Do NOT directly construct:
    Square / RoundedRectangle               →  ChalkBox (or ChalkPanel for frames)
    Code (Manim's syntax-highlighter)       →  ChalkCode
    Arrow / CurvedArrow                     →  ChalkArrow
    NumberLine                              →  ChalkAxis
    Axes                                    →  ChalkAxes
    Matrix                                  →  ChalkMatrix
    Line as annotation connector            →  Callout (it owns the connector)
    Circle used as a graph node             →  NetworkNode
    Stacked MathTex lines (derivation)      →  EquationGroup

MOVES (chalkboard_moves) — named pedagogical animation patterns:

  reveal_with_emphasis(self, item)                — single focal reveal, breathy motion
  compare_split(self, left, right, add_divider=)  — two-up reveal with a vertical divider
  focus_zoom(self, target, others=[...])          — scale an on-screen target, dim the periphery
  morph_show_equivalence(self, a, b)              — A → B in place (same thing, two forms)
  cascade_reveal(self, items, lag_name=)          — LaggedStart sequence with rhythm
  progressive_step(self, items, current_idx)      — algorithm step (mechanical snap motion)
  annotate_and_pause(self, callout, hold_sec=)    — reveal + hold + dim
  chapter_transition(self, from_items, "Label")   — grand-motion thematic boundary (returns the label)
  derivation_step(self, eq, i)                    — reveal line i of an EquationGroup by morphing
                                                    line i-1 into it (i=0 writes the first line)
  transform_equation(self, a, b)                  — rewrite an expression in place; symbols that
                                                    survive stay put (b must be pre-positioned)
  emphasize_term(self, expr, term, role=)         — color + circle one isolated term of a MathTex

  PREFER moves over raw self.play(FadeIn(x), run_time=N.N). Each move
  carries motion intent; a raw run_time literal loses it.

TEMPLATES (chalkboard_templates) — whole-scene choreography; you only fill a `beats` dict:

  AlgorithmTemplate, CodeTemplate, CompareTemplate, DerivationTemplate,
  HowtoTemplate, TimelineTemplate. Each: Template(self, theme=..., beats={...}).render_all(_d)
  emits every begin_segment, animation and wait; construct() then only adds
  end_layout_check() + the final FadeOut. When a TEMPLATE block appears in the
  request, use that template. For a math-heavy script whose core is one chain
  of equalities, DerivationTemplate is usually the best scene even without one:
    DerivationTemplate(self, theme="chalkboard", beats={
        "title": r"Why $\frac{\dd}{\dd x} x^2 = 2x$",
        "lines": [r"f'(x) &= \lim_{h \to 0} \frac{(x+h)^2 - x^2}{h}",
                  r"&= \lim_{h \to 0} \frac{2xh + h^2}{h}",
                  r"&= \lim_{h \to 0} \left( 2x + h \right)",
                  r"&= 2x"],
        "steps": [{"note": "Definition of the derivative"},
                  {"note": r"Expand $(x+h)^2$"},
                  {"note": r"Divide through by $h$"},
                  {"emphasize": "2x"}],        # steps[k] annotates lines[k]
        "colors": {"h": "accent_cool"},
    }).render_all(_d)

MATH — every formula must look like a textbook typeset it:

  - ALL math goes through MathTex: math_tex(...), EquationGroup, ChalkMatrix,
    ChalkBox(..., math=True), Callout(..., math=True), or tex() for prose with
    $inline math$. NEVER put math in Text: no Text("x^2"), Text("f'(x) = 2x"),
    Text("√2"), Text("x²"), Text("∫ f dx"), Text("a ≤ b"). Text is for words only.
  - Titles and labels that contain math are math too: a title like
    "Why the derivative of e^x is e^x" must be tex(r"Why $\frac{\dd}{\dd x} e^x = e^x$", size="title")
    (templates accept `$...$` in their "title" and text fields and typeset it).
  - Write real LaTeX: \frac{a}{b} (never a/b for a displayed fraction), \cdot for
    products, \left( \right) around tall contents, \sqrt{}, \sum_{i=1}^{n},
    \int_a^b f(x) \, \dd x (thin space before the differential), \lim_{h \to 0},
    \vec{v} or \mathbf{v}, \hat{x}, \, and \; for spacing. Always raw strings: r"...".
  - Derivatives use an UPRIGHT d via the house macro \dd:
    \frac{\dd y}{\dd x}, \frac{\dd}{\dd x} f(x), \int f(x) \, \dd x. Partials: \partial.
  - The house LaTeX preamble (already loaded for every MathTex/Tex) provides
    amsmath, amssymb, mathtools, bm, siunitx (\SI{9.81}{m/s^2}, \si{\kilo\gram}),
    cancel (\cancel{h}), mathrsfs (\mathscr{L}), dsfont, and the macros
    \R \N \Z \Q \C (number sets), \E (expectation), \dd (differential),
    \Var \Cov \tr \rank \argmax \argmin. Use them: \E[X], \Var(X), x \in \R^n.
  - Sizes stay on the token scale: display math size="math" (the default), inline
    or small labels size="body" / "caption". Never font_size=<int>.
  - Color terms by MEANING with roles, not by hand: math_tex(r"F = m a",
    colors={"m": "accent_cool", "a": "focus_primary"}) or
    EquationGroup([...], colors={"x": "focus_primary"}). Keys match whole tokens
    and never the inside of a command (colors={"h": ...} leaves \right alone).
    Keep the palette small: one or two colored terms per equation.
    To point at a term later, isolate it (isolate=["2x"]) and call
    emphasize_term(self, eq.lines[i], "2x").
  - Multi-line derivations: EquationGroup with align*-style "&" before the
    relation, so every "=" lines up; continuation lines start with "&=":
      eq = EquationGroup([r"(a+b)^2 &= (a+b)(a+b)",
                          r"&= a^2 + ab + ba + b^2",
                          r"&= a^2 + 2ab + b^2"])
      eq.move_to(ORIGIN)            # position BEFORE revealing; do not self.add(eq)
      derivation_step(self, eq, 0)  # then 1, 2, ... one per beat
    Without "&", lines auto-align on their first =, \le, \approx, \implies.
    To fade a derivation out later, FadeOut its lines: *[FadeOut(l) for l in eq.lines]
    (only the revealed ones are on screen; seg_items.extend(eq.lines) is fine
    once all are revealed).
  - Keep one derivation on screen at a time; if it would be taller than ~5 units
    or wider than ~12, scale the EquationGroup down (eq.scale_to_fit_width(12))
    BEFORE revealing it, or split it across segments.
  - Plots: ChalkAxes + ax.plot(...). Label curves with math_tex(..., size="caption")
    placed with .next_to(...). Areas / Riemann sums: ax.area(...).
  - Labels never sit on a curve (the layout check flags label_on_curve). A curve
    label goes next_to(curve.get_end(), RIGHT) or beside a point the curve has
    already left; a threshold or value line uses ax.hline(y, label=...) /
    ax.vline(x, label=...), which put the label outside the plot. Never put a
    value label next_to(ax.c2p(x, y)) where the curve passes near (x, y).

TOKENS (chalkboard_tokens) — every color, size, gap, stroke, run_time:

    t = T(theme="chalkboard")
    t.bg, t.body                            — shorthand for the most common
    t.surface("bg" | "bg_subtle" | "grid")  — canvas surfaces
    t.role("focus_primary" | "focus_secondary" | "context_muted" |
           "accent_warm" | "accent_cool" | "accent_meta" |
           "body" | "stroke" | "stroke_muted")
                                            — semantic colors
    t.type("display" | "title" | "heading" |
           "body" | "caption" | "code" | "micro" | "math")
                                            — font_size by role
    t.space("xs" | "sm" | "md" | "lg" | "xl")     — buff / gap
    t.stroke_width("hair" | "normal" | "bold")    — stroke width
    t.motion("snap" | "emphasis" | "settle" | "grand")
                                            — {run_time, rate_func} dict
    t.lag("cascade" | "quick")              — LaggedStart lag_ratio

  FORBIDDEN in scene code:
    - Raw hex colors:      "#FFFFFF", ManimColor("#xxxxxx"), `WHITE`, `RED`,
                           `BLUE`, `GREEN`, `YELLOW`, `ORANGE`, `PURPLE`, etc.
                           Use t.surface(...) or t.role(...).
    - Raw font_size ints:  font_size=24, font_size=32.
                           Use font_size=t.type("...").
    - Raw buff floats:     buff=0.5, buff=0.85.
                           Use buff=t.space("...").
    - Raw stroke_width:    stroke_width=3, stroke_width=5.
                           Use stroke_width=t.stroke_width("...").
    - Raw run_time inside scene code:  self.play(FadeIn(x), run_time=1.0)
                           Use a move, OR self.play(anim, **resolve_motion(t.motion("...")))
                           (the 0.5s cleanup/teardown FadeOuts in the scaffold are the exception)
    - Math in Text:        Text("x^2"), Text("π r²"), Text("dy/dx"). Use math_tex / tex.

  ROLE SEMANTICS — pick by what the element MEANS, not what color it should be:
    focus_primary   — the magnet. The viewer's eye should land here.
    focus_secondary — the paired/tracked element. Never the magnet; always alongside.
    context_muted   — past steps, dimmed states, supporting text that's there but not THE point.
    accent_warm     — error, heat, warning, outlier.
    accent_cool     — data, signal, measured value.
    accent_meta     — tertiary annotation. Useful, but not the lesson.
    body            — default text/shape color for this theme.

  MOTION SEMANTICS — every animation gets a NAMED motion:
    motion_snap     — state-change tick. Mechanical, no breath. Algorithm steps.
    motion_emphasis — focal reveal. The viewer should LOOK. Default reveal.
    motion_settle   — final state of a sequence. Let it land. Equation morphs.
    motion_grand    — chapter/topic boundary. Ceremonial. Once or twice per scene at most.

DESIGN PATTERNS — annotated exemplars. Imitate them when the script calls for these beats:

EXEMPLAR 1 — Algorithm step-through (binary search):

  t = T(theme="chalkboard")
  values = [3, 7, 11, 14, 18, 22]

  # ChalkBox, not RoundedRectangle. role="body": the cells start equal;
  # progressive_step picks the focal cell per step.
  cells = [ChalkBox(str(v), role="body", width=1.0) for v in values]
  row = VGroup(*cells).arrange(RIGHT, buff=t.space("sm"))   # arrange returns the group
  row.move_to(UP * 0.3)
  seg_items.extend(cells)

  # One array, so lag_name="quick" (near-simultaneous). "cascade" is for
  # ordered points that should each land distinctly.
  cascade_reveal(self, cells, lag_name="quick")

  # Algorithm transitions are mechanical: progressive_step uses motion_snap.
  progressive_step(self, cells, current_idx=2)

  # Tertiary insight → accent_meta; it points at the cell being examined.
  note = Callout("Compare with target", cells[2].get_top(),
                 role="accent_meta", offset=UP * t.space("lg"))
  seg_items.append(note)
  annotate_and_pause(self, note, hold_sec=1.5)

EXEMPLAR 2 — Side-by-side comparison (CPU vs GPU):

  t = T(theme="chalkboard")
  # Panels sit in the LEFT and RIGHT zones; inner edges stay clear of x = ±0.5.
  cpu_panel = ChalkPanel(title="CPU", width=5.4, height=4.5)
  cpu_panel.move_to(LEFT * 3.5)
  gpu_panel = ChalkPanel(title="GPU", width=5.4, height=4.5)
  gpu_panel.move_to(RIGHT * 3.5)
  seg_items.extend([cpu_panel, gpu_panel])
  compare_split(self, cpu_panel, gpu_panel, add_divider=True)

  # Dim CPU, enlarge GPU: the eye goes to GPU without losing the contrast.
  focus_zoom(self, gpu_panel, others=[cpu_panel])

  # Anchor at body_center (below the title), never get_center().
  note = Callout("Thousands of cores", gpu_panel.body_center,
                 role="focus_secondary", offset=DOWN * t.space("md"))
  seg_items.append(note)
  reveal_with_emphasis(self, note)

EXEMPLAR 3 — Code walkthrough (recursive function):

  t = T(theme="chalkboard")
  src = '''def factorial(n):
      if n <= 1:
          return 1
      return n * factorial(n - 1)'''
  code = ChalkCode(src, language="python")   # handles the Code API internally
  code.move_to(LEFT * 3.0 + UP * 0.3)
  seg_items.append(code)
  reveal_with_emphasis(self, code)

  # Lines already on screen are MUTATED, not revealed, so no move covers it:
  # raw self.play(LaggedStart(...)) with resolve_motion keeps token timing.
  self.play(LaggedStart(*[l.animate.set_color(t.role("context_muted")) for l in code.code_lines],
                        lag_ratio=t.lag("quick")),
            **resolve_motion(t.motion("snap")))
  self.play(code.code_lines[3].animate.set_color(t.role("focus_primary")),
            **resolve_motion(t.motion("emphasis")))
  note = Callout("Recursive case", code.code_lines[3].get_right(),
                 role="accent_meta", offset=RIGHT * t.space("lg"))
  seg_items.append(note)
  annotate_and_pause(self, note, hold_sec=2.0)

EXEMPLAR 4 — Calculus: area under a curve, then the derivation:

  t = T(theme="chalkboard")
  ax = ChalkAxes([0, 3, 1], [0, 9, 3], x_length=5.0, y_length=3.6,
                 x_label="x", y_label="f(x)")
  ax.move_to(LEFT * 3.6 + DOWN * 0.3)
  curve = ax.plot(lambda x: x**2, role="focus_secondary", x_range=[0, 3])
  area = ax.area(curve, x_range=[0, 2], role="accent_cool")
  label = math_tex(r"f(x) = x^2", size="body", role="focus_secondary")
  label.next_to(curve.get_end(), RIGHT, buff=t.space("sm"))   # past the curve's end, off it
  seg_items.extend([ax, curve, area, label])
  reveal_with_emphasis(self, ax, motion_name="snap")     # scaffolding: no fanfare
  self.play(Create(curve), **resolve_motion(t.motion("emphasis")))
  cascade_reveal(self, [area, label], lag_name="cascade")

  # The derivation lives in the RIGHT zone; relations align on "=".
  eq = EquationGroup([
      r"\int_0^2 x^2 \, \dd x &= \left[ \frac{x^3}{3} \right]_0^2",
      r"&= \frac{8}{3} - 0",
      r"&= \frac{8}{3}",
  ], colors={r"\frac{8}{3}": "focus_primary"}, size="body")
  eq.move_to(RIGHT * 3.4)
  derivation_step(self, eq, 0)
  derivation_step(self, eq, 1)
  derivation_step(self, eq, 2)
  seg_items.extend(eq.lines)

EXEMPLAR 5 — Word-level sync with cue markers. Narration for segment 1:
  "Start from the [[1]] limit definition of the derivative. Plug in [[2]] e to the x,
   and [[3]] factor e to the x out of the numerator."
  (cues at ~0.6s, ~3.2s, ~4.9s)

  self.next_segment(1, duration=_d[1], clear=seg_items)
  seg_items = []
  eq = EquationGroup([
      r"f'(x) &= \lim_{h \to 0} \frac{f(x+h) - f(x)}{h}",
      r"&= \lim_{h \to 0} \frac{e^{x+h} - e^x}{h}",
      r"&= e^x \lim_{h \to 0} \frac{e^h - 1}{h}",
  ], colors={"e^x": "focus_primary"})
  eq.move_to(ORIGIN)              # build and place BEFORE the cues
  self.cue(1)                     # "limit definition"
  derivation_step(self, eq, 0)
  self.cue(2)                     # "e to the x"
  derivation_step(self, eq, 1)
  self.cue(3)                     # "factor": 1.7s after cue 2, so one settle-length move
  derivation_step(self, eq, 2)
  seg_items.extend(eq.lines)

KNOWN API PITFALLS (verified on v0.21.0):
- Brace.get_text(*text) does NOT accept font_size (TypeError) — scale the returned object: lbl = brace.get_text("x"); lbl.scale(0.8). For math labels use brace.get_tex(r"...").
- VGroup.arrange() returns the group, so `VGroup(*items).arrange(RIGHT, buff=t.space("sm"))` chains fine.
- Always pass run_time as a keyword arg (or **resolve_motion(...)).
- Never use VGroup(*self.mobjects) — self.mobjects can contain non-VMobjects; use *[FadeOut(m) for m in self.mobjects] instead
- DASHED is not a Manim constant — use DashedLine(start, end, ...) for dashed lines.
- self.wait(0) raises ValueError — always guard: _r = max(0.0, _d[i] - X); if _r > 0: self.wait(_r)
- self.play() with zero animations raises ValueError — never star-unpack a filtered list that may be empty; build it first and guard with `if anims:`.
- Pad _d with: _d = _d + [2.0] * max(0, N - len(_d)) where N is the literal integer segment count
- Segment numbers and _d indices are both 0-based — Segment 0 → _d[0], Segment 1 → _d[1]
- When pointer labels AND a descriptive text line both appear below an array, leave at least buff=t.space("lg") between the array and the description so they don't collide.
- Never animate a label's position relative to a pointer using j_ptr.copy().next_to(...) inside .animate — .animate captures positions before the frame; pass the destination coordinate directly: j_label.animate.move_to(cells[j].get_top() + UP * t.space("md"))
- Never construct Code directly (use ChalkCode). For reference: Code takes `code_string=` NOT `code=`, and `paragraph_config={"font_size": N}` NOT `font_size=` (both TypeError); lines are `code_obj.code_lines[i]` (`.code` does not exist) and there is no `Code.highlight_lines()` — use set_color on code_lines.
- NEVER use `+` or `-` between two Mobjects or between a Mobject and a number: `Mobject.__add__` raises NotImplementedError at render time. Group with VGroup(a, b); position with .move_to(...), .next_to(...), .shift(direction * scalar). Never write a stray `+ 0` after a mobject expression.
- NEVER call GrowArrow on a ChalkArrow (it is a VGroup with no points of its own → "Cannot call Mobject.get_start for a Mobject with no points"). Use Create(arrow) or FadeIn(arrow).
- TransformMatchingTex only matches whole MathTex parts; use derivation_step / transform_equation, which fall back to glyph-shape matching when the lines are not split into {{ }} parts.
- MathTex(..., color=X) / Tex(..., color=X) leave the glyphs WHITE in 0.21 (the constructor color is not applied). math_tex()/tex()/EquationGroup apply color correctly; if you ever build MathTex directly, call .set_color(...) afterwards.
- Text(...).color reads back as black in 0.21 even when constructed with color=; compare/copy colors via the token you passed, not via .color.

LAYOUT RULES — required for every scene:

Canvas: x ∈ [−7.11, +7.11], y ∈ [−4.0, +4.0]. Named anchor points:
  title_anchor  = UP * 3.4               # persistent title — full width (title band, y > 2.9)
  left_anchor   = LEFT * 3.5 + UP * 0.3  # code, arrays, diagrams, plots (left half)
  right_anchor  = RIGHT * 3.5 + UP * 0.3 # callouts, derivations, annotations (right half)
  center_anchor = ORIGIN                  # full-width single element
  bottom_anchor = DOWN * 3.3             # captions, one-line notes

Placement rules:
1. Anchor primary elements with move_to(zone_anchor) or to_edge(). Avoid chaining
   next_to() through more than 2 elements from a fixed point — drift accumulates.
2. When LEFT and RIGHT zones are both populated, keep left content within x < −0.5
   and right content within x > +0.5. Never let the two zones overlap.
   BOUNDING BOX CHECK — required before placing any horizontal group:
   For N elements of width W placed side by side with leftmost center at x_0:
     right_edge = x_0 + (N − 0.5) × W
   This right_edge must be < −0.5 for LEFT zone content. With x_0 = −3.5 the LEFT
   zone holds about 3 units of total width (3 cols × W=0.9, or 2 cols × W=1.4).
   A full-width row (array, timeline) belongs in the center with nothing in the
   side zones at the same time.
3. Keep every element at least ~0.3 units inside the canvas edge; scale wide
   groups down (.scale_to_fit_width(12.5)) instead of letting them run off-frame.

CLEAN SLATE rule — mandatory at every segment boundary:
4. Track all mobjects added in a segment in a list as you create them:
     seg_items = []
     elem = ...; reveal_with_emphasis(self, elem); seg_items.append(elem)
5. Every segment after the first starts by clearing the previous one:
     self.next_segment(N, duration=_d[N], clear=seg_items)
     seg_items = []
   (an empty seg_items is fine: next_segment then just waits for the narration.)
6. The persistent title is NEVER added to seg_items.
7. A multi-segment element (e.g. a derivation spanning segments 1–3) is excluded from
   seg_items; FadeOut it explicitly at the segment where it is no longer needed.
8. Leaving mobjects from a prior segment on screen while starting a new segment is the
   primary cause of visual overlap — treat this rule as strictly as the self.wait(0) guard.
9. Call self.begin_segment(0, duration=_d[0]) / self.next_segment(N, duration=_d[N], clear=seg_items)
   right after each '# ── Segment N:' comment. Call self.end_layout_check() at the end of construct() BEFORE the
   final FadeOut. These are required — code_validator will reject code missing them.

REQUIRED SCAFFOLD — every scene must follow this structure exactly:

from chalkboard_base import ChalkboardSceneBase
from chalkboard_tokens import T
from chalkboard_components import (
    ChalkBox, ChalkArrow, ChalkCode, Callout, StepCounter,
    ChalkAxis, ChalkAxes, ChalkPanel, ChalkBadge, EquationGroup,
    ChalkMatrix, NetworkNode, math_tex, tex, resolve_motion,
)
from chalkboard_moves import (
    reveal_with_emphasis, compare_split, focus_zoom,
    morph_show_equivalence, cascade_reveal, progressive_step,
    annotate_and_pause, chapter_transition,
    derivation_step, transform_equation, emphasize_term,
)
from chalkboard_templates import (
    AlgorithmTemplate, CodeTemplate, CompareTemplate,
    DerivationTemplate, HowtoTemplate, TimelineTemplate,
)
from manim import *
import json
from pathlib import Path

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
        _d = [s["actual_duration_sec"] for s in _seg_data]
        _d = _d + [2.0] * max(0, N - len(_d))  # N = total segment count (integer)

        # Bind tokens to the run's theme; no hex/font/timing literal below.
        t = T(theme="chalkboard")    # replace "chalkboard" with the theme in the request
        self.camera.background_color = t.bg

        # ── Segment 0: <title> ──
        self.begin_segment(0, duration=_d[0])
        seg_items = []
        # ... build mobjects, then per marker: self.cue(k); <move or play> ...

        # ── Segment 1: <title> ──
        self.next_segment(1, duration=_d[1], clear=seg_items)
        seg_items = []
        # ... build mobjects, then per marker: self.cue(k); <move or play> ...

        # ── End ──
        self.end_layout_check()
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)

When a template drives the whole scene, construct() is just the duration
loading, `t = T(...)`, the background, `Template(self, theme=..., beats={...}).render_all(_d)`,
then `self.end_layout_check()` and the final FadeOut (no '# ── Segment N:' blocks
of your own; the template calls begin_segment, and it calls self.cue itself:
each segment's main reveal lands on [[1]], a second element (callout,
right-hand point, next derivation line) on [[2]]).

Respond with JSON only: {"manim_code": "<complete Python code as string>"}"""

# TEMPLATE_SPECS teach the agent to INSTANTIATE template classes from
# chalkboard_templates and pass `beats` data instead of hand-rolling the
# scene. Each template owns layout, motion and segment-boundary discipline.
TEMPLATE_SPECS = {
    "algorithm": (
        "TEMPLATE — AlgorithmTemplate (chalkboard_templates).\n"
        "Use for: array step-through (binary search, sorting, DP traces) where state\n"
        "advances cell-by-cell and each step focuses one cell.\n\n"
        "Construct it in construct() AFTER setting `t = T(theme=...)` and pass _d:\n"
        "    AlgorithmTemplate(self, theme=\"chalkboard\", beats={\n"
        "        \"title\": \"Binary Search\",\n"
        "        \"values\": [3, 7, 11, 14, 18, 22],\n"
        "        \"steps\": [\n"
        "            # one entry per narration segment AFTER segment 0 (the intro)\n"
        "            {\"active_idx\": 2, \"callout\": \"Compare with target\"},\n"
        "            {\"active_idx\": 4, \"callout\": \"Move to the right half\"},\n"
        "            {\"active_idx\": 4, \"callout\": \"Found it\"},\n"
        "            # optional value_change: {\"idx\": 1, \"to\": 9} rewrites a cell first\n"
        "        ],\n"
        "        # optional \"math\": True when values are LaTeX (\"x_1\", r\"\\frac{1}{2}\")\n"
        "    }).render_all(_d)\n\n"
        "The template owns: title, a centered row of equal-width cells, a step counter\n"
        "(top-right), per-step progressive_step (mechanical snap), an optional Callout per\n"
        "step (role accent_meta; the previous one is cleared), segment timing.\n"
        "Segment 0 is the intro; steps[i] maps to segment i+1. Provide len(_d) - 1 steps\n"
        "or fewer (trailing segments hold the last state)."
    ),
    "code": (
        "TEMPLATE — CodeTemplate (chalkboard_templates).\n"
        "Use for: line-by-line code walkthrough (function explanation, algorithm\n"
        "pseudo-code, config-file walk).\n\n"
        "Construct it in construct() and pass _d to render_all:\n"
        "    CodeTemplate(self, theme=\"chalkboard\", beats={\n"
        "        \"title\": \"Factorial\",\n"
        "        \"code_string\": '''def factorial(n):\n"
        "    if n <= 1:\n"
        "        return 1\n"
        "    return n * factorial(n - 1)''',\n"
        "        \"language\": \"python\",  # optional, defaults to python\n"
        "        \"steps\": [\n"
        "            # one entry per narration segment AFTER segment 0 (full reveal)\n"
        "            {\"line_indices\": [0],    \"callout\": \"Function signature\"},\n"
        "            {\"line_indices\": [1, 2], \"callout\": \"Base case\"},\n"
        "            {\"line_indices\": [3],    \"callout\": \"Recursive case\"},\n"
        "        ],\n"
        "    }).render_all(_d)\n\n"
        "The template owns: ChalkCode in the LEFT zone (code_string= and paragraph_config\n"
        "handled internally), per-step line highlight (other lines muted), an optional\n"
        "Callout in the right column. line_indices are zero-indexed and validated;\n"
        "several lines per step are allowed."
    ),
    "compare": (
        "TEMPLATE — CompareTemplate (chalkboard_templates).\n"
        "Use for: two-up side-by-side comparison (CPU vs GPU, recursion vs iteration,\n"
        "mean vs median) where the viewer weighs paired traits.\n\n"
        "Construct it in construct() and pass _d to render_all:\n"
        "    CompareTemplate(self, theme=\"chalkboard\", beats={\n"
        "        \"title\": \"CPU vs GPU\",      # optional\n"
        "        \"left\":  {\"title\": \"CPU\", \"points\": [\n"
        "            \"Few powerful cores\", \"Low-latency dispatch\"]},\n"
        "        \"right\": {\"title\": \"GPU\", \"points\": [\n"
        "            \"Thousands of cores\", \"High-throughput batching\"]},\n"
        "    }).render_all(_d)\n\n"
        "The template owns: two ChalkPanels in the LEFT/RIGHT zones with a divider\n"
        "(compare_split), matched pairs revealed one per segment (left focus_secondary,\n"
        "right focus_primary), text wrapped to the panel width. Points may contain $math$.\n"
        "left['points'] and right['points'] MUST have equal length (matched pairs).\n"
        "Segment 0 establishes the panels; segments 1..N reveal pairs[0..N-1]."
    ),
    "howto": (
        "TEMPLATE — HowtoTemplate (chalkboard_templates).\n"
        "Use for: numbered step list (setup guide, recipe, procedure, proof outline).\n\n"
        "Construct it in construct() and pass _d to render_all:\n"
        "    HowtoTemplate(self, theme=\"chalkboard\", beats={\n"
        "        \"title\": \"Set Up a Virtual Environment\",\n"
        "        \"steps\": [\n"
        "            {\"label\": \"Create it\", \"description\": \"python -m venv .venv\",\n"
        "             \"callout\": \"One per project\"},\n"
        "            {\"label\": \"Activate it\", \"description\": \"source .venv/bin/activate\"},\n"
        "            {\"label\": \"Install\",    \"description\": \"pip install -r requirements.txt\"},\n"
        "        ],\n"
        "    }).render_all(_d)\n\n"
        "The template owns: all steps revealed at segment 0, dimmed; per segment one\n"
        "step becomes active (focus_primary highlight) and the previous one is marked done;\n"
        "ChalkBadge numbers; an optional Callout in the right column.\n"
        "Segment 0 introduces the list; segments 1..N activate steps[0..N-1]."
    ),
    "timeline": (
        "TEMPLATE — TimelineTemplate (chalkboard_templates).\n"
        "Use for: chronological event sequence (history of X, evolution of an idea,\n"
        "milestone walkthrough).\n\n"
        "Construct it in construct() and pass _d to render_all:\n"
        "    TimelineTemplate(self, theme=\"chalkboard\", beats={\n"
        "        \"title\": \"History of Computing\",\n"
        "        \"events\": [\n"
        "            {\"date\": \"1945\", \"label\": \"ENIAC\",\n"
        "             \"description\": \"First general-purpose computer\"},\n"
        "            {\"date\": \"1969\", \"label\": \"ARPANET\",\n"
        "             \"description\": \"First packet-switched network\",\n"
        "             \"callout\": \"Four nodes at first\"},\n"
        "            {\"date\": \"1991\", \"label\": \"Web\",\n"
        "             \"description\": \"Tim Berners-Lee at CERN\"},\n"
        "        ],\n"
        "    }).render_all(_d)\n\n"
        "The template owns: a horizontal ChalkAxis, one event marker per segment on the\n"
        "axis ticks (Dot + date + label + description, alternating above/below), earlier\n"
        "events receding to context_muted, an optional caption per event.\n"
        "Segment 0 establishes the axis; segments 1..N reveal events[0..N-1]."
    ),
    "derivation": (
        "TEMPLATE — DerivationTemplate (chalkboard_templates).\n"
        "Use for: a step-by-step math derivation or algebraic proof (calculus rules,\n"
        "solving equations, expanding expressions, probability identities, physics\n"
        "formulas) where each narration beat adds one more line.\n\n"
        "Construct it in construct() and pass _d to render_all:\n"
        "    DerivationTemplate(self, theme=\"chalkboard\", beats={\n"
        "        \"title\": r\"The Power Rule for $x^3$\",\n"
        "        \"lines\": [\n"
        "            r\"\\frac{\\dd}{\\dd x} x^3 &= \\lim_{h \\to 0} \\frac{(x+h)^3 - x^3}{h}\",\n"
        "            r\"&= \\lim_{h \\to 0} \\frac{3x^2 h + 3x h^2 + h^3}{h}\",\n"
        "            r\"&= \\lim_{h \\to 0} \\left( 3x^2 + 3xh + h^2 \\right)\",\n"
        "            r\"&= 3x^2\",\n"
        "        ],\n"
        "        \"steps\": [   # optional; steps[k] annotates lines[k]\n"
        "            {\"note\": \"Definition of the derivative\"},\n"
        "            {\"note\": r\"Expand $(x+h)^3$ and cancel $x^3$\"},\n"
        "            {\"note\": r\"Divide every term by $h$\"},\n"
        "            {\"emphasize\": \"3x^2\", \"note\": r\"Every term with $h$ vanishes\"},\n"
        "        ],\n"
        "        \"colors\": {\"h\": \"accent_cool\"},   # optional term -> role\n"
        "    }).render_all(_d)\n\n"
        "The template owns: the title (math in $...$ is typeset), one aligned\n"
        "EquationGroup (relations line up on '&'), one new line per segment morphing\n"
        "out of the previous line (derivation_step: unchanged symbols stay put),\n"
        "earlier lines dimmed, a justification caption at the bottom, optional term\n"
        "emphasis. Segment k reveals lines[k]; extra lines land in the final segment.\n"
        "Lines are LaTeX in raw strings; use \\dd for upright differentials, \\frac,\n"
        "\\left( \\right)."
    ),
}

# Theme prompt blocks are derived from the design tokens (single source of
# truth: docker/chalkboard_tokens.py), so prompt and renderer never drift.
THEME_SPECS = {
    theme: render_prompt_block(theme)
    for theme in ("chalkboard", "light", "colorful")
}


SCHEMA = {
    "type": "object",
    "properties": {"manim_code": {"type": "string"}},
    "required": ["manim_code"],
    "additionalProperties": False,
}


def _format_segments(segments: list[dict], pace=None, speed: float = 1.0) -> str:
    pace = pace or pacing.resolve_pace()
    n = len(segments)
    header = (f"Total segments: {n} (use _d[0] through _d[{max(0, n-1)}]). "
              f"[[k]] = cue marker: call self.cue(k) right before the animation for it. "
              f"[[beat]] = a silent pause in the voice (no call needed; keep the frame still). "
              f"[[ch: Title]] = a chapter starts at the cue right after it (no call needed; that cue's visual introduces the chapter's item). "
              f"Times include the run's pacing ({pace.name}): a short silent lead-in, natural pauses, "
              f"and a {pace.hold:.1f}s silent hold after each segment's last word.")
    lines = [header]
    for i, seg in enumerate(segments):  # 0-based
        est = pacing.estimate_segment(seg, pace, speed)
        duration = est["actual_duration_sec"]
        raw = segment_cue_text(seg)
        _, offsets = parse_cues(raw)
        cues = est.get("cues") or []
        cue_info = ""
        if offsets:
            parts = [f"[[{k}]]~{cues[k - 1]:.1f}s" for k in sorted(offsets)
                     if k - 1 < len(cues) and cues[k - 1] is not None]
            cue_info = f" — cues {', '.join(parts)}"
        speech_end = est.get("speech_end_sec")
        hold_info = (f" — last word ~{speech_end:.1f}s, then hold still"
                     if isinstance(speech_end, (int, float)) else "")
        lines.append(f"  Segment {i} — est. {duration:.1f}s — use _d[{i}] at runtime{cue_info}{hold_info}: {raw}")
    return "\n".join(lines)


# ── Output budgets and chunking ───────────────────────────────────────────────
# Measured 2026-10-09 (runs af68f322, 6c8582b4: 8-segment lecture recaps with
# ~44k tokens of PDF context): at effort high the scene call used all of the
# old 48000-token budget, sometimes before writing any code. Replayed with room
# to spare, the af68f322 call used 80,863 output tokens in 694 s: ~22.5k for
# the answer (an 845-line scene) and ~58k of thinking. max_tokens caps
# thinking + text and is not billed unless used, so the single-shot call gets
# the model's whole output cap (128000 on Opus 5.5). Wide scripts are written
# in parts up front, and a single-shot scene that runs out of room is rewritten
# in parts. Each plan/part call has the full
# budget ladder of call_json_budgeted: the model's cap (128000 on Opus 5.5),
# then one effort level lower. With SCENE_CHUNKING=off the single-shot call
# gets that ladder instead.
MANIM_MAX_TOKENS = 128000  # clamped to the model's cap (Models API)
PLAN_MAX_TOKENS = 32000
PART_MAX_TOKENS = 64000
# Write the scene in parts up front when the script is this wide. The measured
# 8-segment scene above needed 63% of the model's cap in one response, so a
# wider one risks a wasted full-cap call. This applies with a template too: the
# af68f322 replay ignored the derivation template and hand-wrote the scene.
CHUNK_MIN_SEGMENTS = 7
CHUNK_MIN_CUE_CHARS = 5000


def _chunking_mode() -> str:
    mode = (os.getenv("SCENE_CHUNKING", "") or "auto").strip().lower()
    return mode if mode in ("auto", "always", "off") else "auto"


def should_chunk(state: PipelineState) -> bool:
    """Write this scene in parts from the start?"""
    mode = _chunking_mode()
    if mode != "auto":
        return mode == "always"
    segs = state.get("script_segments") or []
    chars = sum(len(segment_cue_text(s)) for s in segs)
    return len(segs) >= CHUNK_MIN_SEGMENTS or chars >= CHUNK_MIN_CUE_CHARS


def _scene_request(state: PipelineState) -> str:
    """Topic, timed segments, script and theme: what every scene call sees."""
    return (
        f"Create a Manim animation for this educational script.\n\n"
        f"Topic: {state['topic']}\n\n"
        f"Narration segments with timings:\n"
        f"{_format_segments(state['script_segments'], pacing.resolve_pace(state.get('pace')), state.get('speed', 1.0))}\n\n"
        f"Full script for context:\n{state['script']}\n\n"
        f"{THEME_SPECS[state.get('theme', 'chalkboard')]}"
    )


def _with_context(user_msg: str | list, context_blocks) -> str | list:
    if not context_blocks:
        return user_msg
    content = [{
        "type": "text",
        "text": "The following files are provided as source material. Use them to inform what the animation should visualize:",
    }]
    content.extend(context_blocks)
    if isinstance(user_msg, list):
        content.extend(user_msg)
    else:
        content.append({"type": "text", "text": user_msg})
    return content


def scene_context_mode() -> str:
    """Which scene calls see the run's context files (MANIM_CONTEXT):
    "off" (default): none. The fact-checked script already carries what the
    source says, and the animator works from it; the files cost a 44k-token
    PDF per plan call and invited re-deriving the lecture. "plan": the plan
    call and a single-response scene see them (the 0.6.0 behaviour)."""
    mode = (os.getenv("MANIM_CONTEXT", "") or "off").strip().lower()
    return mode if mode in ("off", "plan") else "off"


async def manim_agent(state: PipelineState, client=None, context_blocks=None) -> dict:
    if scene_context_mode() == "off":
        context_blocks = None
    if client is None:
        client = get_client(pdf=has_pdf(context_blocks))

    if state.get("code_feedback") and state.get("manim_code"):
        # A scene written in parts is revised part by part. The parts come from
        # the state, or are read back from the code (visual-QA revisions start
        # from scene.py on disk).
        parts = state.get("scene_parts") or scene_parts.split_assembled(
            state["manim_code"], len(state["script_segments"]), state.get("theme", "chalkboard"))
        if parts and _is_assembled(state, parts):
            return await _revise_parts({**state, "scene_parts": parts}, client)
    if should_chunk(state):
        return await _write_in_parts(state, client, context_blocks)

    user_msg = _scene_request(state)
    template = state.get("template")
    if template and template in TEMPLATE_SPECS:
        user_msg += f"\n\n{TEMPLATE_SPECS[template]}"
    # The request is the cached prefix a revision of this scene shares.
    content = [cached_text(user_msg)]

    prior_code = (state.get("manim_code") or "").strip()
    if state.get("code_feedback") and prior_code:
        # Revision round: targeted edits on the prior code converge faster and
        # don't reintroduce already-fixed bugs the way a full rewrite does.
        content.append({"type": "text", "text": (
            f"Previous attempt had issues. Below is the prior scene code; "
            f"MAKE MINIMAL TARGETED CHANGES to fix the cited issues. "
            f"Preserve all unchanged code exactly as-is. Do NOT rewrite the scene "
            f"from scratch unless the issues indicate a fundamentally wrong "
            f"approach (e.g., 5+ violations across all segments).\n\n"
            f"Issues to fix:\n{state['code_feedback']}\n\n"
            f"Prior scene code:\n```python\n{prior_code}\n```"
        )})
        content[1:1] = _frame_blocks(state)
    elif state.get("code_feedback"):
        content.append({"type": "text", "text": f"Issues to address:\n{state['code_feedback']}"})

    try:
        # Streams (budget > 16000): scene code is long and thinking adds latency.
        data, _ = await call_json_budgeted(
            "manim", label="manim_agent", timeout=TIMEOUT_MANIM_AGENT,
            max_tokens=MANIM_MAX_TOKENS, client=client,
            system=SYSTEM_PROMPT, cache_system=True,
            role="manim_fix" if (state.get("code_feedback") and prior_code) else "manim",
            content=_with_context(content, context_blocks), schema=SCHEMA,
            # Out of room once: writing in parts beats a bigger single response
            # (each part is a smaller task, and the parts run concurrently).
            max_steps=3 if _chunking_mode() == "off" else 1,
        )
    except ClaudeOutOfRoom as e:
        if _chunking_mode() == "off":
            raise
        print(f"  [manim_agent] one response cannot hold this scene ({e}); writing it in parts")
        telemetry.emit("budget", {"agent": "manim", "label": "manim_agent", "reason": "out_of_room",
                                  "fallback": "scene_parts"})
        return await _write_in_parts(state, client, context_blocks)

    return {"manim_code": data["manim_code"], "scene_parts": None, "scene_plan": None,
            "status": "validating"}


# ── Writing the scene in parts ────────────────────────────────────────────────

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "style": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "visuals": {"type": "string"},
                    "end_state": {"type": "string"},
                },
                "required": ["index", "visuals", "end_state"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "style", "segments"],
    "additionalProperties": False,
}

PART_SCHEMA = {
    "type": "object",
    "properties": {
        "imports": {"type": "array", "items": {"type": "string"}},
        "code": {"type": "string"},
        # Revisions answer with search/replace edits to the part's current code
        # (a few lines each) instead of the whole method; new parts leave it empty.
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"find": {"type": "string"}, "replace": {"type": "string"}},
                "required": ["find", "replace"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["imports", "code", "edits"],
    "additionalProperties": False,
}

# The part instructions are the same text for every part (the part number is
# given after them), so the system prompt, the scene request and these
# instructions form one prefix shared by every part call, cached once.
PART_MODE = r"""PART MODE (overrides the REQUIRED SCAFFOLD and the response format of the system prompt):
This scene is too long for one response, so it is written in parts that are
assembled into one ChalkboardScene. You write ONE part: a single method,
written at top level (no class around it), with exactly this signature
(K = your part number, given at the end of this request):

    def part_K(self, t, _d, seg_items):

- `t` is the bound theme tokens, `_d` the segment durations, `seg_items` what the
  previous part left on screen (empty for part 0).
- Part 0 starts with `# ── Segment 0: <title> ──`, then
  `self.begin_segment(0, duration=_d[0])` and `seg_items = []`. It creates the
  persistent title from the plan as `self.title_mob` (revealed once, never added to
  seg_items). Later parts never touch self.title_mob.
- Every other segment N, INCLUDING the first segment of a later part, starts with
  `# ── Segment N: <title> ──`, then `self.next_segment(N, duration=_d[N], clear=seg_items)`
  and `seg_items = []`.
- End the method with `return seg_items`: everything your part leaves on screen
  except self.title_mob. Nothing else crosses a part boundary.
- The assembler writes the imports (everything in the REQUIRED SCAFFOLD import
  block is available), the class, construct(), the segments.json loading, the
  T(...) binding, the background, end_layout_check() and the final FadeOut. Do not
  write any of those. Helper functions go inside the method. If you need another
  import (e.g. `import numpy as np`), list the statement in "imports".
- Follow the VISUAL PLAN for your segments (roles per concept, layout, what is on
  screen at the end of each segment) so the parts read as one video. Every other
  rule of the system prompt (cues, pacing, layout zones, clean slate, tokens, math)
  applies.

Respond with JSON only: {"imports": ["<import statement>", ...], "code": "<the method source>", "edits": []}
(When you are asked to fix an existing part, the request says how to use "edits".)"""


def _template_note(state: PipelineState) -> str:
    template = state.get("template")
    if not template:
        return ""
    return (f"\n\nThe request asked for the {template} template. A whole-scene template cannot be "
            f"split into parts, so compose its look from components and moves instead (for a "
            f"derivation: EquationGroup + derivation_step, one line per beat).")


def _parts_overview(ranges) -> str:
    return "; ".join(f"part {k} = segments {a}-{b}" for k, (a, b) in enumerate(ranges))


async def _plan_scene(state: PipelineState, ranges, client, context_blocks) -> dict:
    msg = (
        _scene_request(state) + _template_note(state)
        + f"\n\nPLAN ONLY, NO CODE. This scene will be written in {len(ranges)} parts by separate "
          f"calls ({_parts_overview(ranges)}), each seeing only this plan and the script. "
          f"Write the visual plan that keeps them one coherent video:\n"
          f"- title: the persistent on-screen title (plain words; $...$ for math).\n"
          f"- style: the shared visual language, concretely: which role (focus_primary, "
          f"accent_cool, ...) each recurring concept gets, recurring components and where they "
          f"sit (zones), naming of recurring symbols, motion habits.\n"
          f"- segments: for every segment index, what is shown at each cue (component + zone), "
          f"using numbers and facts from the script{' and the source material' if context_blocks else ''}, "
          f"and end_state: "
          f"what is on screen when the segment ends. Nothing except the title crosses a part "
          f"boundary, so the last segment of each part ends self-contained."
    )
    if state.get("code_feedback"):
        msg += (f"\n\nA previous version of this scene had these issues; plan so they cannot recur:\n"
                f"{state['code_feedback']}")
    # One plan call per scene: its prefix is never reused, so it is not cached.
    data, _ = await call_json_budgeted(
        "manim", label="manim_agent plan", timeout=TIMEOUT_MANIM_AGENT,
        max_tokens=PLAN_MAX_TOKENS, client=client, role="manim_plan",
        system=SYSTEM_PROMPT, content=_with_context(msg, context_blocks), schema=PLAN_SCHEMA,
    )
    return data


def _part_prefix(state: PipelineState, plan: dict, ranges) -> list[dict]:
    """The cached prefix every part call of this scene shares (after the system
    prompt): the scene request, then the plan and the part instructions."""
    plan_text = (f"VISUAL PLAN (shared by all parts):\n{json.dumps(plan, indent=1)}" if plan else
                 "No visual plan is available for this revision: keep your part's existing look.")
    return [
        cached_text(_scene_request(state) + _template_note(state)),
        cached_text(f"{plan_text}\n\nThe scene has {len(ranges)} parts ({_parts_overview(ranges)}).\n\n"
                    f"{PART_MODE}"),
    ]


def _part_request(state: PipelineState, plan: dict, ranges, k: int) -> list[dict]:
    a, b = ranges[k]
    return _part_prefix(state, plan, ranges) + [{
        "type": "text",
        "text": f"Write part {k}: `def part_{k}(self, t, _d, seg_items):` covering segments {a} to {b}.",
    }]


def _with_note(content: list[dict], note: str) -> list[dict]:
    last = content[-1]
    return content[:-1] + [{**last, "text": last["text"] + note}]


# A visual-QA fix shows the fixer the frames QA judged (the frames of its own
# segments): QA describes a collision in words, and the fixer reading only code
# kept missing some (run fe12e799: the same "+ ion box covers an atom" error
# survived both fix rounds). 1280 px frames are ~1.2k input tokens each.
MAX_FIX_FRAMES = 4


def _frame_blocks(state: PipelineState, segments=None) -> list[dict]:
    frames = [f for f in state.get("qa_frames") or []
              if segments is None or f.get("segment") in segments]
    if len(frames) > MAX_FIX_FRAMES:
        step = len(frames) / MAX_FIX_FRAMES
        frames = [frames[int(i * step)] for i in range(MAX_FIX_FRAMES)]
    blocks: list[dict] = []
    for f in frames:
        try:
            data = base64.standard_b64encode(Path(f["path"]).read_bytes()).decode()
        except (OSError, KeyError, TypeError):
            continue
        t = f.get("t")
        blocks.append({"type": "text", "text": f"QA frame of segment {f.get('segment')}"
                                               + (f" at t={t:.1f}s" if isinstance(t, (int, float)) else "")})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}})
    if blocks:
        blocks.insert(0, {"type": "text", "text": "The rendered frames the visual check judged (what the issues below describe):"})
    return blocks


async def _write_part(state, plan, ranges, k, client, *, revise: str | None = None,
                      current: str | None = None, on_start=None, frames: list[dict] | None = None) -> dict:
    """One part method. Returns {"segments": [a, b], "code", "imports"}.

    A revision (``revise`` set, ``current`` = the part's code) may answer with
    search/replace edits; edits that do not apply get one re-ask for the whole
    method."""
    a, b = ranges[k]
    label = f"manim_agent part {k + 1}/{len(ranges)}" + (" revision" if revise else "")
    content = _part_request(state, plan, ranges, k)
    if revise:
        content = _with_note(content, revise)
    if frames:
        content = content[:-1] + frames + content[-1:]
    last_error = None
    data: dict = {}
    for _ in range(2):
        attempt = content if last_error is None else _with_note(
            content, f"\n\nYour previous answer for this part was not usable: {last_error}. "
                     f"Return exactly one method `def part_{k}(self, t, _d, seg_items):` "
                     f"in \"code\" (the whole method) and an empty \"edits\" list.")
        data, _ = await call_json_budgeted(
            "manim", label=label, timeout=TIMEOUT_MANIM_AGENT, max_tokens=PART_MAX_TOKENS,
            client=client, system=SYSTEM_PROMPT, cache_system=True,
            content=attempt, schema=PART_SCHEMA, on_start=on_start,
            role="manim_fix" if revise else "manim",
        )
        on_start = None
        try:
            code = data.get("code", "") or ""
            if current is not None and data.get("edits") and not code.strip():
                code = scene_parts.apply_edits(current, data["edits"])
            code = scene_parts.extract_method(code, k)
            imports = scene_parts.clean_imports(data.get("imports"))
            if current is not None and not imports:
                imports = list((state.get("scene_parts") or [{}] * (k + 1))[k].get("imports") or [])
            return {"segments": [a, b], "code": code, "imports": imports}
        except scene_parts.PartError as e:
            last_error = str(e)
            print(f"  [{label}] unusable part ({e}); asking again")
    # Keep the raw text: the validators report what is wrong and the revision
    # round comes back to this part.
    return {"segments": [a, b], "code": data.get("code", "") or (current or ""),
            "imports": scene_parts.clean_imports(data.get("imports"))}


async def _staggered(factories):
    """Run coroutine factories concurrently, but start the first one alone and
    the rest once it has started streaming: a cache entry becomes readable only
    then, so the others read the shared prefix instead of each writing it.
    Each factory takes an ``on_start`` callback."""
    if not factories:
        return []
    loop = asyncio.get_running_loop()
    started = asyncio.Event()

    def on_start():
        loop.call_soon_threadsafe(started.set)

    first = asyncio.ensure_future(factories[0](on_start))
    if len(factories) > 1:
        waiter = asyncio.ensure_future(started.wait())
        await asyncio.wait({first, waiter}, timeout=STAGGER_MAX_WAIT_S,
                           return_when=asyncio.FIRST_COMPLETED)
        waiter.cancel()
    rest = [asyncio.ensure_future(f(None)) for f in factories[1:]]
    return list(await asyncio.gather(first, *rest))


# How long the other part calls wait for the first one to start streaming
# before they start anyway (a slow or failing first call must not stall them).
STAGGER_MAX_WAIT_S = 90.0


def _parts_result(state: PipelineState, parts: list[dict], plan: dict) -> dict:
    code, _ = scene_parts.assemble(parts, len(state["script_segments"]), state.get("theme", "chalkboard"))
    return {"manim_code": code, "scene_parts": parts, "scene_plan": plan, "status": "validating"}


async def _write_in_parts(state: PipelineState, client, context_blocks) -> dict:
    ranges = scene_parts.part_ranges(len(state["script_segments"]))
    print(f"  [manim_agent] writing the scene in {len(ranges)} parts ({_parts_overview(ranges)})")
    telemetry.emit("scene_parts", {"status": "planning", "parts": len(ranges)})
    plan = await _plan_scene(state, ranges, client, context_blocks)
    # The parts share the plan, so they are written concurrently.
    parts = await _staggered([
        (lambda on_start, k=k: _write_part(state, plan, ranges, k, client, on_start=on_start))
        for k in range(len(ranges))
    ])
    telemetry.emit("scene_parts", {"status": "assembled", "parts": len(ranges)})
    return _parts_result(state, list(parts), plan)


def _is_assembled(state: PipelineState, parts: list[dict]) -> bool:
    """The current scene is still exactly the assembly of these parts (nobody
    replaced it with a single-shot scene since)."""
    try:
        code, _ = scene_parts.assemble(parts, len(state["script_segments"]), state.get("theme", "chalkboard"))
    except Exception:
        return False
    return code == state.get("manim_code")


async def _revise_parts(state: PipelineState, client) -> dict:
    """Revision round on a scene written in parts: only the parts the feedback
    is about are revised, each with its own current code, as search/replace
    edits (or the whole method when the fix restructures it)."""
    parts = [dict(p) for p in state["scene_parts"]]
    plan = state.get("scene_plan") or {}
    ranges = [tuple(p["segments"]) for p in parts]
    _, spans = scene_parts.assemble(parts, len(state["script_segments"]), state.get("theme", "chalkboard"))
    targets = scene_parts.route_feedback(state["code_feedback"], parts, spans)
    print(f"  [manim_agent] revising part(s) {', '.join(str(k) for k in targets)} of {len(parts)}")
    telemetry.emit("scene_parts", {"status": "revising", "parts": len(parts), "targets": targets})

    def note(k):
        lo, hi = spans[k]
        return (
            f"\n\nYour part had issues. Below is its current code. MAKE MINIMAL TARGETED CHANGES "
            f"to fix the issues that concern your segments ({ranges[k][0]}-{ranges[k][1]}). "
            f"Line numbers in the issues refer to the assembled scene, where your method spans "
            f"lines {lo}-{hi}. Ignore issues about other segments.\n"
            f"Answer with \"edits\": search/replace pairs applied in order to the current code. "
            f"Each \"find\" is an exact, contiguous excerpt of the current code (whitespace and "
            f"indentation included) that occurs exactly once; keep it short but unique (a few "
            f"lines). \"replace\" is its new text. Leave \"code\" empty and \"imports\" empty "
            f"unless you need a new import. Only when the fix rewrites most of the method, "
            f"return the whole method in \"code\" and no edits instead.\n\n"
            f"Issues:\n{state['code_feedback']}\n\n"
            f"Current code of part {k}:\n```python\n{parts[k]['code']}\n```"
        )

    new = await _staggered([
        (lambda on_start, k=k: _write_part(state, plan, ranges, k, client, revise=note(k),
                                           current=parts[k]["code"], on_start=on_start,
                                           frames=_frame_blocks(state, range(ranges[k][0], ranges[k][1] + 1))))
        for k in targets
    ])
    for k, p in zip(targets, new):
        parts[k] = p
    return _parts_result(state, parts, plan)
