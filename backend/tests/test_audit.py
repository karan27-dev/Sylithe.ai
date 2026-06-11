import json


def test_chain_valid_after_records(audit):
    audit.record("a", n=1)
    audit.record("b", n=2)
    audit.record("c", token="sk-" + "x" * 30)
    assert audit.verify_chain()


def test_tampering_detected(audit, settings):
    audit.record("a", n=1)
    audit.record("b", n=2)
    path = settings.audit_log_path
    lines = open(path).read().splitlines()
    entry = json.loads(lines[0])
    entry["n"] = 999  # tamper
    lines[0] = json.dumps(entry)
    open(path, "w").write("\n".join(lines) + "\n")
    assert not audit.verify_chain()
