from sylithe.devops.conflicts import ConflictType, Strategy, parse_conflicts, resolve

FORMATTING = """def add(a, b):
<<<<<<< HEAD
    return a + b
=======
    return a+b
>>>>>>> feature
"""

LOGIC = """def price(total):
<<<<<<< HEAD
    return total * 1.10
=======
    return total * 1.25
>>>>>>> feature
"""

DEPENDENCY = """{
<<<<<<< HEAD
  "left-pad": "^1.3.0"
=======
  "left-pad": "^2.0.0"
>>>>>>> feature
}
"""


def test_formatting_only_prefers_ours():
    report = parse_conflicts(FORMATTING, "src/math.py")
    assert len(report.conflicts) == 1
    c = report.conflicts[0]
    assert c.type is ConflictType.FORMATTING
    assert c.strategy is Strategy.PREFER_OURS
    assert report.auto_resolvable


def test_logic_divergence_escalates():
    report = parse_conflicts(LOGIC, "src/billing.py")
    c = report.conflicts[0]
    assert c.type is ConflictType.LOGIC
    assert c.strategy is Strategy.ESCALATE
    assert not report.auto_resolvable


def test_dependency_files_never_auto_resolve():
    report = parse_conflicts(DEPENDENCY, "package.json")
    assert report.conflicts[0].type is ConflictType.DEPENDENCY
    assert report.conflicts[0].strategy is Strategy.ESCALATE


def test_test_files_prefer_incoming():
    report = parse_conflicts(LOGIC, "tests/test_billing.py")
    assert report.conflicts[0].type is ConflictType.TEST
    assert report.conflicts[0].strategy is Strategy.PREFER_THEIRS


def test_resolve_applies_strategy_and_leaves_escalations():
    resolved, report = resolve(FORMATTING + LOGIC, "src/mixed.py")
    # formatting conflict resolved to HEAD side
    assert "return a + b" in resolved
    assert "return a+b" not in resolved
    # logic conflict left untouched with markers intact
    assert "<<<<<<< HEAD" in resolved
    assert "total * 1.25" in resolved
    assert len(report.conflicts) == 2


def test_resolve_with_override():
    resolved, _ = resolve(LOGIC, "src/billing.py", override=Strategy.PREFER_THEIRS)
    assert "total * 1.25" in resolved
    assert "<<<<<<<" not in resolved
