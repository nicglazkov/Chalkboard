# pipeline/agents/code_validator.py
import ast
from pipeline.ast_guards import run_guards
from pipeline.llm import call_json_budgeted
from pipeline.retry import TimeoutExhausted, TIMEOUT_CODE_VALIDATOR
from pipeline.state import PipelineState, ValidationResult

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approved", "needs_revision"]},
        "feedback": {"type": "string"},
    },
    "required": ["verdict", "feedback"],
    "additionalProperties": False,
}


# Manim CE mobject constructors. `Mobject.__add__` / `__sub__` raise
# NotImplementedError, so `Text(...) + 0` or `Text(a) + Text(b)` is valid
# Python that crashes at render time, slipping past ast.parse() and often
# past the Claude review too. A missing entry just makes the check less
# aggressive, never unsafe.
_MOBJECT_CONSTRUCTORS = frozenset({
    # Text
    "Text", "MarkupText", "Tex", "MathTex", "Title", "BulletedList", "Paragraph",
    "Code", "DecimalNumber", "Integer", "Variable",
    # Lines / arrows
    "Line", "DashedLine", "Arrow", "Vector", "DoubleArrow", "CurvedArrow", "CurvedDoubleArrow",
    # Shapes
    "Rectangle", "Square", "RoundedRectangle", "Circle", "Ellipse", "Annulus", "AnnularSector",
    "Polygon", "RegularPolygon", "Triangle", "Star", "Sector", "Arc", "ArcBetweenPoints",
    "Dot", "SmallDot", "Cross", "Cutout",
    # Decorators
    "Brace", "BraceBetweenPoints", "BraceLabel", "BraceLabelText", "BraceText",
    # Composites
    "VGroup", "Group", "VMobject", "Mobject",
    # Plotting
    "NumberLine", "Axes", "ThreeDAxes", "NumberPlane", "PolarPlane", "ComplexPlane",
    "FunctionGraph", "ParametricFunction", "ImplicitFunction",
    # Image / svg / 3D
    "ImageMobject", "SVGMobject", "Surface", "Sphere", "Cube", "Prism",
    # Matrix
    "Matrix", "MobjectMatrix", "DecimalMatrix", "IntegerMatrix",
    # Chalkboard design system
    "ChalkBox", "ChalkArrow", "ChalkCode", "Callout", "StepCounter", "ChalkAxis",
    "ChalkAxes", "ChalkPanel", "ChalkBadge", "EquationGroup", "ChalkMatrix",
    "NetworkNode", "math_tex", "tex",
})


def _innermost_callable_name(node: ast.AST) -> str | None:
    """Walk a method chain like `Foo(...).bar().baz()` down to its leftmost
    Name. None when the chain doesn't bottom out at a bare name."""
    while True:
        if isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Attribute):
            node = node.value
        else:
            break
    return node.id if isinstance(node, ast.Name) else None


def _is_mobject_call_chain(node: ast.AST) -> bool:
    """Does this expression construct a Mobject (possibly through a method
    chain)? Variables holding a Mobject are not detected (no dataflow)."""
    name = _innermost_callable_name(node)
    return name is not None and name in _MOBJECT_CONSTRUCTORS


def _scan_invalid_mobject_arithmetic(tree: ast.AST) -> str | None:
    """Find `MobjectExpr (+|-) <number | MobjectExpr>`; return feedback or None."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp):
            continue
        if not isinstance(node.op, (ast.Add, ast.Sub)):
            continue
        if not _is_mobject_call_chain(node.left):
            continue
        right_mobj = _is_mobject_call_chain(node.right)
        right_lit = (
            isinstance(node.right, ast.Constant)
            and isinstance(node.right.value, (int, float))
            and not isinstance(node.right.value, bool)
        )
        if right_mobj or right_lit:
            line_no = getattr(node, "lineno", "?")
            return (
                f"line {line_no}: invalid arithmetic on a Manim Mobject. "
                "`Mobject.__add__` / `__sub__` raise NotImplementedError, "
                "so `mobj + 0`, `mobj + 2.5`, and `Text(a) + Text(b)` are "
                "valid Python but crash at render time. Group with "
                "`VGroup(a, b)`; position with `.move_to(np.array([x,y,z]))`, "
                "`.next_to(other, direction, buff=...)`, or "
                "`.shift(direction * scalar)`. Never write a stray `+ 0` "
                "or any other arithmetic at the end of a mobject "
                "construction or method chain."
            )
    return None


async def code_validator(state: PipelineState, client=None) -> dict:
    code = state["manim_code"]
    attempts = state["code_attempts"]

    # Step 1: syntax check (free, fast — no Claude call)
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {
            "code_feedback": f"Syntax error: {e}",
            "code_attempts": attempts + 1,
            "claude_review_failures": 0,
            "code_feedback_advisory": False,
        }

    # Step 1b: Mobject arithmetic (render-time NotImplementedError).
    mobj_arith_feedback = _scan_invalid_mobject_arithmetic(tree)
    if mobj_arith_feedback:
        return {
            "code_feedback": mobj_arith_feedback,
            "code_attempts": attempts + 1,
            "claude_review_failures": 0,
            "code_feedback_advisory": False,
        }

    # Step 1c: deterministic AST guards (scaffold contract, design-system
    # enforcement, math typesetting, known render-time crash patterns).
    # Catches mechanical bugs before paying for a Claude call.
    guards_feedback = run_guards(tree, code)
    if guards_feedback:
        return {
            "code_feedback": guards_feedback,
            "code_attempts": attempts + 1,
            "claude_review_failures": 0,
            "code_feedback_advisory": False,
        }

    # Step 2: semantic review via Claude. The structural rules (scene base,
    # begin_segment, end_layout_check, wait literals, colors, imports) are
    # already enforced above, so the review focuses on meaning and APIs.
    user_msg = (
        f"Review this Manim CE v0.21.0 code for correctness and coherence with the script.\n\n"
        f"Script:\n{state['script']}\n\n"
        f"Manim code:\n{code}\n\n"
        f"Check: Does the animation visualize the script? Are Manim CE APIs used correctly? "
        f"Is the class named ChalkboardScene?\n\n"
        f"The scene is built on the Chalkboard design system. These are REAL, importable "
        f"APIs (do NOT flag them as unknown or undefined):\n"
        f"- chalkboard_tokens.T: t.role(...), t.surface(...), t.type(...), t.space(...), "
        f"t.stroke_width(...), t.motion(...), t.lag(...), t.bg, t.body\n"
        f"- chalkboard_components: ChalkBox, ChalkArrow, ChalkCode (.code_lines), Callout, "
        f"StepCounter (.advance()), ChalkAxis, ChalkAxes (.plot/.area/.tangent/.dot_at/.c2p/.hline/.vline), "
        f"ChalkPanel (.body_center/.body_top/.body_bottom), ChalkBadge, EquationGroup "
        f"(.lines/.focus(i)), ChalkMatrix, NetworkNode, math_tex(...), tex(...), resolve_motion(...); "
        f"every component has .highlight(role) and .mute()\n"
        f"- chalkboard_moves: reveal_with_emphasis, compare_split, focus_zoom, "
        f"morph_show_equivalence, cascade_reveal, progressive_step, annotate_and_pause, "
        f"chapter_transition, derivation_step, transform_equation, emphasize_term "
        f"(each takes the scene as first argument and plays its own animations)\n"
        f"- chalkboard_templates: AlgorithmTemplate, CodeTemplate, CompareTemplate, "
        f"DerivationTemplate, HowtoTemplate, TimelineTemplate — Template(self, theme=..., "
        f"beats={{...}}).render_all(_d) emits every begin_segment/animation/wait itself, so a "
        f"template-driven scene has no '# ── Segment N:' blocks of its own; that is correct.\n"
        f"- ChalkboardSceneBase: self.begin_segment(n, duration=...), self.next_segment(n, "
        f"duration=..., clear=items) (holds through the narration and its silent hold, starts segment n, fades items), "
        f"self.segment_time_left(), self.speech_time_left() (seconds until the segment's last word), "
        f"self.end_layout_check(), self.cue(k) (holds until cue "
        f"marker [[k]] of the current segment's narration is spoken, so the next animation "
        f"starts on that word; template scenes call it inside the template), self.has_cue(k). "
        f"Narration sync is automatic, so a segment without a trailing remainder wait is correct.\n"
        f"- The house LaTeX preamble defines \\dd, \\R, \\N, \\Z, \\Q, \\C, \\E, \\Var, \\Cov, "
        f"\\tr, \\rank, \\argmax, \\argmin and loads amsmath, mathtools, siunitx, cancel, bm.\n\n"
        f"CONFIRMED CORRECT Manim APIs (do NOT flag these as errors):\n"
        f"- code_obj.code_lines[i] — the i-th line (VGroup); .code attribute does not exist\n"
        f"- VGroup(...).arrange(...) returns the group, so chaining is fine\n"
        f"- *[FadeOut(m) for m in self.mobjects] is the correct teardown\n"
        f"- self.wait(0) is invalid; guard with: _r = max(0.0, x); if _r > 0: self.wait(_r)\n\n"
        f"Cleanup check: For each segment block after the first (marked by '# ── Segment N:' "
        f"comments where N > 0), verify the code clears the previous segment's tracked mobjects "
        f"(e.g. self.play(*[FadeOut(m) for m in seg_items], ...)) BEFORE introducing new content, "
        f"unless an element is intentionally carried across segments. If a segment piles new "
        f"content on top of the previous segment's content, return needs_revision.\n\n"
        f"Math check: every formula must be typeset with LaTeX (math_tex / tex / EquationGroup / "
        f"ChalkMatrix / math=True), never with Text; LaTeX must be valid (balanced braces, "
        f"\\left/\\right pairs, raw strings). Flag math that would render wrong or illegibly.\n\n"
        f"Bounding box check: for any horizontal row of N boxes/cards of width W whose leftmost "
        f"center is at x_0: right_edge = x_0 + (N − 0.5) × W. If right_edge > −0.5 while "
        f"right-zone elements are present in the same segment, the row overflows into the right "
        f"zone — return needs_revision."
    )

    try:
        data, _ = await call_json_budgeted(
            "code_validator", label="code_validator", timeout=TIMEOUT_CODE_VALIDATOR,
            max_tokens=16000, content=user_msg, schema=SCHEMA, client=client,
        )
    except TimeoutExhausted as e:
        # The review is advisory and the deterministic checks above passed;
        # the headless layout dry-run is the real gate. Don't lose the run.
        print(f"  [code_validator] review unavailable, continuing to the layout check ({e})")
        return {"code_feedback": None, "code_attempts": attempts, "code_feedback_advisory": False}

    result = ValidationResult.model_validate(data)
    if result.verdict == "needs_revision":
        # Claude's review is advisory: the AST guards above and the headless
        # layout dry-run are the deterministic gates. Count its rejections
        # separately so a nit-picking review cannot burn the retry budget meant
        # for real bugs (graph routing proceeds after CLAUDE_REVIEW_ADVISORY_LIMIT).
        return {
            "code_feedback": result.feedback,
            "claude_review_failures": state.get("claude_review_failures", 0) + 1,
            "code_feedback_advisory": True,
        }
    else:
        # Clear code_feedback on approval so _after_code_validator routes to render_trigger
        return {
            "code_feedback": None,
            "code_attempts": attempts,
            "code_feedback_advisory": False,
        }
