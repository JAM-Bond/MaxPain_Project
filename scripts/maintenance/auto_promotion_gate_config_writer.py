"""Auto-promotion pipeline — safe writer for scripts/qualifier/gate_config.py.

Source-of-truth file for the cycle qualifier. This writer:
  - Parses gate_config.py with the `ast` module
  - Finds the named COHORT_* list literal
  - Mutates the list (add / remove a ticker symbol)
  - Rewrites the file in-place using line-based slicing (preserving
    surrounding comments / blank lines / per-cohort docstrings outside
    the list literal)
  - Atomic via tmpfile + os.replace
  - Idempotent: adding a name that exists is a no-op; removing one that
    doesn't exist is a no-op
  - Validates with `ast.parse` after write — never leaves the file
    syntactically broken

Structure → cohort constant mapping:
  - bull_put       → COHORT_BULL_PUT
  - bear_call      → COHORT_BEAR_CALL
  - inverted_fly   → COHORT_INVERTED_FLY_SINGLE  (standalone IF; PAIR list is curated)
  - zebra          → COHORT_ZEBRA_TIER2          (TIER1 is curated)

Per pre-reg §8 safety: caller must invoke `lib.auto_promotion.check_safety_thresholds`
on the candidate change set BEFORE calling apply_changes() — this module
will refuse to write if the safety-check result is included as failing.
"""
from __future__ import annotations

import ast
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path.home() / "MaxPain_Project"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GATE_CONFIG_PATH = ROOT / "scripts/qualifier/gate_config.py"

STRUCTURE_TO_COHORT = {
    "bull_put": "COHORT_BULL_PUT",
    "bear_call": "COHORT_BEAR_CALL",
    "inverted_fly": "COHORT_INVERTED_FLY_SINGLE",
    "zebra": "COHORT_ZEBRA_TIER2",
}

log = logging.getLogger("auto_promotion_gate_config_writer")


@dataclass
class CohortChange:
    """One pending mutation: add or remove `ticker` from cohort for `structure`."""
    ticker: str
    structure: str
    action: str   # "PROMOTE" or "DEMOTE"
    reason: str

    @property
    def cohort_name(self) -> str:
        return STRUCTURE_TO_COHORT[self.structure]


# ──── Read current cohorts ────────────────────────────────────────────────

def read_cohort_members(path: Path = GATE_CONFIG_PATH) -> dict[str, list[str]]:
    """Parse gate_config.py and return {cohort_name: [members]} for every
    COHORT_* assignment whose RHS is a list of string literals.
    """
    tree = ast.parse(path.read_text())
    out: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t for t in node.targets if isinstance(t, ast.Name)]
        if len(targets) != 1:
            continue
        name = targets[0].id
        if not name.startswith("COHORT_"):
            continue
        rhs = node.value
        if not isinstance(rhs, ast.List):
            continue
        members = []
        for elt in rhs.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                members.append(elt.value)
        out[name] = members
    return out


# ──── Apply mutations ─────────────────────────────────────────────────────

def _find_cohort_node(tree: ast.Module, cohort_name: str) -> ast.Assign | None:
    """Return the ast.Assign node for `cohort_name = [...]`, or None."""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == cohort_name:
                    if isinstance(node.value, ast.List):
                        return node
    return None


def _new_list_literal(members: list[str], comment_suffix: str | None = None) -> str:
    """Render a new list literal as a single line, e.g. `["A", "B", "C"]`.
    For long lists, wraps to multi-line. Always closes with `]`.
    """
    if not members:
        return "[]" + (f"  # {comment_suffix}" if comment_suffix else "")
    # One-line if short
    one_line = "[" + ", ".join(f'"{m}"' for m in members) + "]"
    if comment_suffix:
        one_line += f"  # {comment_suffix}"
    if len(one_line) <= 100:
        return one_line
    # Multi-line: 6 names per line, indented 4 spaces
    lines = ["["]
    chunk = []
    for m in members:
        chunk.append(f'"{m}"')
        if len(chunk) == 6:
            lines.append("    " + ", ".join(chunk) + ",")
            chunk = []
    if chunk:
        lines.append("    " + ", ".join(chunk) + ",")
    closing = "]"
    if comment_suffix:
        closing += f"  # {comment_suffix}"
    lines.append(closing)
    return "\n".join(lines)


def _apply_change_set_to_source(source: str,
                                  changes: list[CohortChange]) -> tuple[str, dict]:
    """Apply a set of changes to the source. Returns (new_source, summary_dict).

    summary_dict has counts per cohort and a list of applied / skipped (noop) actions.
    """
    tree = ast.parse(source)
    source_lines = source.splitlines(keepends=True)

    # Group changes by cohort
    by_cohort: dict[str, list[CohortChange]] = {}
    for ch in changes:
        by_cohort.setdefault(ch.cohort_name, []).append(ch)

    summary = {"applied": [], "noop": [], "per_cohort_after": {}}

    # We rewrite from BOTTOM to TOP so earlier line numbers remain valid as
    # we edit lower portions of the file.
    cohort_nodes = []
    for cohort_name in by_cohort:
        node = _find_cohort_node(tree, cohort_name)
        if node is None:
            log.warning("cohort %s not found in source — skipping", cohort_name)
            continue
        cohort_nodes.append((cohort_name, node))
    cohort_nodes.sort(key=lambda x: x[1].lineno, reverse=True)

    today_str = date.today().isoformat()

    for cohort_name, node in cohort_nodes:
        # Gather current members from the AST (authoritative)
        current = []
        for elt in node.value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                current.append(elt.value)
        members = list(current)
        for ch in by_cohort[cohort_name]:
            if ch.action == "PROMOTE":
                if ch.ticker in members:
                    summary["noop"].append((ch.ticker, ch.structure, "PROMOTE (already in cohort)"))
                else:
                    members.append(ch.ticker)
                    summary["applied"].append((ch.ticker, ch.structure, "PROMOTE"))
            elif ch.action == "DEMOTE":
                if ch.ticker not in members:
                    summary["noop"].append((ch.ticker, ch.structure, "DEMOTE (not in cohort)"))
                else:
                    members.remove(ch.ticker)
                    summary["applied"].append((ch.ticker, ch.structure, "DEMOTE"))
            else:
                summary["noop"].append((ch.ticker, ch.structure, f"unknown action {ch.action}"))

        if members == current:
            summary["per_cohort_after"][cohort_name] = len(members)
            continue

        # Build the replacement source lines
        comment = f"auto-promotion update {today_str}"
        new_list_src = _new_list_literal(members, comment_suffix=comment)
        # ast nodes are 1-indexed and end_lineno may not exist on older
        # Python; for 3.11 we can use end_lineno + end_col_offset reliably.
        start_line = node.value.lineno  # line of `[`
        end_line = node.value.end_lineno  # line of `]`
        start_col = node.value.col_offset
        # Take everything BEFORE the `[` on the start line as a prefix,
        # everything AFTER the `]` on the end line as a suffix.
        prefix_on_start = source_lines[start_line - 1][:start_col]
        # end_col_offset is the column AFTER the `]`
        end_col = node.value.end_col_offset
        suffix_on_end = source_lines[end_line - 1][end_col:]
        # Drop any inline-comment in the original suffix (we'll rebuild it
        # via _new_list_literal so we don't accumulate stacked comments).
        if "#" in suffix_on_end:
            # Preserve trailing newline if present
            nl = "\n" if suffix_on_end.endswith("\n") else ""
            suffix_on_end = nl
        new_text = prefix_on_start + new_list_src + suffix_on_end
        # Replace lines [start_line-1 : end_line] inclusive with new_text
        source_lines[start_line - 1:end_line] = [new_text]
        summary["per_cohort_after"][cohort_name] = len(members)

    new_source = "".join(source_lines)
    return new_source, summary


def apply_changes(changes: list[CohortChange],
                   path: Path = GATE_CONFIG_PATH,
                   dry_run: bool = False,
                   safety_violations: list[str] | None = None,
                   ) -> dict:
    """Apply a list of CohortChange to gate_config.py.

    Returns a summary dict with:
      ok: bool
      reason: str  (HALTED reason if not ok)
      summary: dict (per-cohort counts + applied/noop lists)
      new_source: str (if dry_run or ok)

    If `safety_violations` is non-empty, HALTS without writing and returns
    ok=False, reason="safety violations: ...".
    """
    result = {"ok": False, "reason": "", "summary": {}, "new_source": None}
    if safety_violations:
        result["reason"] = "HALTED — safety violations: " + "; ".join(safety_violations)
        log.error(result["reason"])
        return result

    source = path.read_text()
    new_source, summary = _apply_change_set_to_source(source, changes)
    result["summary"] = summary
    result["new_source"] = new_source

    # Validate before writing
    try:
        ast.parse(new_source)
    except SyntaxError as e:
        result["reason"] = f"HALTED — rewritten source did not parse: {e}"
        log.error(result["reason"])
        return result

    if dry_run:
        result["ok"] = True
        result["reason"] = f"DRY RUN — would have written {len(summary['applied'])} changes"
        return result

    # Atomic write via tmp + os.replace
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new_source)
    os.replace(tmp, path)
    result["ok"] = True
    result["reason"] = f"wrote {len(summary['applied'])} changes to {path}"
    log.info(result["reason"])
    return result


if __name__ == "__main__":
    # CLI for manual / smoke-test invocation.
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--promote", action="append", default=[],
                    help='TICKER:STRUCTURE pair to promote, e.g. AAPL:bull_put')
    ap.add_argument("--demote", action="append", default=[],
                    help='TICKER:STRUCTURE pair to demote')
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    changes = []
    for spec in args.promote:
        t, s = spec.split(":")
        changes.append(CohortChange(ticker=t, structure=s, action="PROMOTE",
                                     reason="manual"))
    for spec in args.demote:
        t, s = spec.split(":")
        changes.append(CohortChange(ticker=t, structure=s, action="DEMOTE",
                                     reason="manual"))
    if not changes:
        # Just print current cohort membership
        for name, members in read_cohort_members().items():
            print(f"  {name}: {len(members)} — {members[:8]}{'...' if len(members) > 8 else ''}")
        sys.exit(0)

    res = apply_changes(changes, dry_run=args.dry_run)
    print(res["reason"])
    print("Applied:")
    for t, s, a in res["summary"].get("applied", []):
        print(f"  {a:10s} {t:6s} {s}")
    for t, s, a in res["summary"].get("noop", []):
        print(f"  NOOP       {t:6s} {s}  ({a})")
    print("Per-cohort after:")
    for n, c in res["summary"].get("per_cohort_after", {}).items():
        print(f"  {n}: {c}")
