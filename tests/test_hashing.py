from judge_compare.hashing import canonical_json_bytes, canonical_sha256


def test_canonical_json_hash_ignores_mapping_insertion_order() -> None:
    first = {"b": [2, 1], "a": {"x": True}}
    second = {"a": {"x": True}, "b": [2, 1]}

    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert canonical_sha256(first) == canonical_sha256(second)
