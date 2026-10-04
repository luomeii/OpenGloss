"""Nightly acceptance: run all PAE acceptance probes in sequence.

Usage: python tests/nightly.py
Exit code 0 = all green; non-zero = something failed. Results appended to
tests/nightly_log.txt (keep last 30 runs).
"""
import io
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = Path(__file__).parent / "nightly_log.txt"

PROBES = [
    ("unit", ["python", "-m", "pytest", "pae_core", "-q"]),
    ("m2", ["python", "-X", "utf8", "tests/m2_acceptance.py"]),
    ("toggle", ["python", "-X", "utf8", "tests/toggle_probe.py"]),
    # 名字如实描述被测对象：5000 词输入的 HTTP 往返 + 前 8 词记账（段 B/C 是同构估算）
    ("e2e_5k_http_first8", ["python", "-X", "utf8", "tests/e2e_latency.py"]),
    # 只跑 100k 档；1M 档不在 nightly（R5-B 会输出 N/A，不得用 100k 数字冒充）
    ("r5_stress_100k", ["python", "-X", "utf8", "tests/r5_stress.py", "--n", "100000"]),
    ("t2_accumulate", ["python", "-X", "utf8", "tests/t2_accumulate.py"]),
    ("t4_filter", ["python", "-X", "utf8", "tests/t4_extract_filter.py"]),
    ("t7_hover", ["python", "-X", "utf8", "tests/t7_hover.py"]),
    ("t8_density", ["python", "-X", "utf8", "tests/t8_density.py"]),
    ("t9_capsurface", ["python", "-X", "utf8", "tests/t9_capsurface.py"]),
    ("t10_observe", ["python", "-X", "utf8", "tests/t10_observe.py"]),
    ("t11_fullloop", ["python", "-X", "utf8", "tests/t11_fullloop.py"]),
    ("t12_memory", ["python", "-X", "utf8", "tests/t12_memory.py"]),
    ("t13_conformance", ["python", "-X", "utf8", "tests/t13_conformance.py"]),
    ("t14_write_push", ["python", "-X", "utf8", "tests/t14_write_push.py"]),
    ("t16_orchestration", ["python", "-X", "utf8", "tests/t16_orchestration.py"]),
    ("t17_permissions", ["python", "-X", "utf8", "tests/t17_permissions.py"]),
    ("t18_mcp", ["python", "-X", "utf8", "tests/t18_mcp.py"]),
    ("t19_scheduler", ["python", "-X", "utf8", "tests/t19_scheduler.py"]),
    ("t21_wiring", ["python", "-X", "utf8", "tests/t21_wiring.py"]),
    ("t22_outcomes", ["python", "-X", "utf8", "tests/t22_outcomes.py"]),
    ("t23_familiarity", ["python", "-X", "utf8", "tests/t23_familiarity.py"]),
    ("t24_sensejudge", ["python", "-X", "utf8", "tests/t24_sensejudge.py"]),
    ("t25_form", ["python", "-X", "utf8", "tests/t25_form.py"]),
    ("t26_portable", ["python", "-X", "utf8", "tests/t26_portable.py"]),
    ("t27_reachability", ["python", "-X", "utf8", "tests/t27_reachability.py"]),
    ("t28_param_freedom", ["python", "-X", "utf8", "tests/t28_param_freedom.py"]),
    ("t29_band_selection", ["python", "-X", "utf8", "tests/t29_band_selection.py"]),
    ("t30_m12c", ["python", "-X", "utf8", "tests/t30_m12c.py"]),
    ("t33_remember", ["python", "-X", "utf8", "tests/t33_remember.py"]),
]
# ⚠️ 往 PROBES 里加测试时：**上一行结尾必须有逗号**（这个坑踩过三次）。

# r5_stress.py 不带 --n 参数跑 100k+1M；nightly 只跑 100k 档，1M 须手动
# （python tests/r5_stress.py --n 1000000）；R5-B 在 100k 档下输出 N/A。
# real_site_probe / t5_persona / t15_agent / r3_runner 依赖外网代理或消耗 LLM 额度，不进 nightly（手动跑）。

# R7 闸门：这几项此前「PASS 但无任何 JSON 工件」，数字只活在日志最后一行。
# 现在工件缺失或未在本次运行中刷新 → 该项判 FAIL。
REQUIRED_ARTIFACTS = {
    "m2": "m2_acceptance.json",
    "toggle": "toggle_probe.json",
    "e2e_5k_http_first8": "e2e_latency.json",
    "r5_stress_100k": "r5_stress.json",
}
ARTIFACTS_DIR = Path(__file__).parent / "artifacts"


def main():
    lines = ["", "=== PAE nightly " + time.strftime("%Y-%m-%d %H:%M:%S") + " ==="]
    failures = []
    for name, cmd in PROBES:
        t0 = time.time()
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=900)
        dt = time.time() - t0
        # 判 PASS/FAIL：exit code + 输出里的显式 PASS/FAIL 标记
        ok = r.returncode == 0 and "FAIL" not in (r.stdout or "")
        out = (r.stdout or "").strip().splitlines()
        tail = out[-1] if out else ""
        want = REQUIRED_ARTIFACTS.get(name)
        art_ok = None
        if want:
            ap = ARTIFACTS_DIR / want
            try:
                art_ok = ap.exists() and ap.stat().st_mtime >= t0 - 5
            except OSError:
                art_ok = False
        if ok and want and not art_ok:
            # 无工件 = 无据可查，不许算 PASS
            ok = False
            tail = f"缺工件 artifacts/{want}（PASS 无数据支撑）| " + tail
        mark = "PASS" if ok else "FAIL"
        lines.append(f"[{mark}] {name} ({dt:.0f}s) {tail}")
        if not ok:
            failures.append(name)
            # 失败必须留证据（否则只能靠猜）
            lines.append("    --- " + name + " 输出尾部 ---")
            for ln in out[-25:]:
                lines.append("    " + ln)
            err = (r.stderr or "").strip().splitlines()
            if err:
                lines.append("    --- " + name + " stderr ---")
                for ln in err[-10:]:
                    lines.append("    " + ln)
    lines.append("NIGHTLY: " + ("PASS" if not failures else "FAIL " + ",".join(failures)))
    text = "\n".join(lines)
    print(text)
    keep = 30
    old = LOG.read_text(encoding="utf-8") if LOG.exists() else ""
    runs = old.split("=== PAE nightly ")
    runs = ["=== PAE nightly " + r for r in runs if r.strip()]
    runs.append(text)
    LOG.write_text("\n".join(runs[-keep:]), encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
