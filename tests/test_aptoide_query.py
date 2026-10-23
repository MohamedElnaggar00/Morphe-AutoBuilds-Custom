import base64

from src import aptoide


def test_q_param_has_no_base64_padding_and_decodes():
    # Aptoide rejects padded base64 for q (SBASE64); padding must be stripped.
    for arch in ("arm64-v8a", "armeabi-v7a"):
        q = aptoide._get_q_param(arch)
        assert q.startswith("&q=")
        value = q[3:]
        assert "=" not in value
        decoded = base64.b64decode(value + "=" * (-len(value) % 4)).decode()
        assert decoded.startswith("myCPU=") and decoded.endswith("&leanback=0")


def test_q_param_universal_is_empty():
    assert aptoide._get_q_param("universal") == ""
