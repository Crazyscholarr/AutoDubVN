# Repro chan doan 2 loi timing (tam thoi - xoa sau khi chay)
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from autodub.timeline import fit_segments_strict

MIN_GAP, MAX_SPEED, MAX_OVERHANG = 0.02, 1.6, 0.75
failures = []

print("=" * 72)
print("CA 1: cau ngan, sau do la khoang lang dai (sparse dialogue)")
print("=" * 72)
# sub i = [10.0, 12.0] -> chi dai 2.0s. Cau ke tiep tan 30.0s.
# Giong TTS doc mat 6.0s.
starts = [10.0, 30.0]
ends   = [12.0, 33.0]
nat    = [6.0, 2.0]
pls = fit_segments_strict(starts, nat, max_speed=MAX_SPEED, min_gap=MIN_GAP,
                          total_duration=40.0, trim_overflow=True, ends=ends,
                          max_overhang=MAX_OVERHANG)
p = pls[0]
print(f"  SRT cua cau 0      : {starts[0]:.2f} -> {ends[0]:.2f}  (dai {ends[0]-starts[0]:.2f}s)")
print(f"  Giong TTS tu nhien : {nat[0]:.2f}s")
print(f"  speed ap dung      : {p.speed:.3f}x   trimmed={p.trimmed}")
print(f"  Giong phat that su : {p.placed_start:.2f} -> {p.placed_end:.2f} (dai {p.final_dur:.2f}s)")
overrun = p.placed_end - ends[0]
ok = overrun <= MAX_OVERHANG + 0.01
print(f"  >>> VUOT QUA SUB   : {overrun:+.2f}s", "  <-- OK, trong tran" if ok else "  <-- LOI")
if not ok:
    failures.append("sparse dialogue vuot tran")

print()
print("=" * 72)
print("CA 2: hai nhan vat noi chong nhau (multi-character)")
print("=" * 72)
# A = [10.0, 15.0], B = [11.0, 16.0] -> sub goc von da chong nhau
starts = [10.0, 11.0, 30.0]
ends   = [15.0, 16.0, 32.0]
nat    = [7.0, 7.5, 1.0]
pls = fit_segments_strict(starts, nat, max_speed=MAX_SPEED, min_gap=MIN_GAP,
                          total_duration=40.0, trim_overflow=True, ends=ends,
                          max_overhang=MAX_OVERHANG)
for i, p in enumerate(pls[:2]):
    name = "A" if i == 0 else "B"
    print(f"  {name}: SRT goc {starts[i]:.2f}->{ends[i]:.2f} | "
          f"giong {p.placed_start:.2f}->{p.placed_end:.2f} "
          f"({p.speed:.2f}x, trimmed={p.trimmed})")
a, b = pls[0], pls[1]
print(f"  >>> Giong A tran sang sau khi B bat dau: {a.placed_end - starts[1]:+.2f}s")
print(f"  >>> Sub ghi lai (use_placed) cua A     : {a.placed_start:.2f} -> {a.placed_end:.2f}")
print(f"      nhung sub B bat dau luc            : {starts[1]:.2f}  <-- DUNG NHU BAN GOC")

print()
print("=" * 72)
print("CA 3: cau dai + khoang lang dai phia sau -> muon het khoang lang")
print("=" * 72)
starts = [5.0, 60.0]
ends   = [8.0, 62.0]
nat    = [25.0, 2.0]
pls = fit_segments_strict(starts, nat, max_speed=MAX_SPEED, min_gap=MIN_GAP,
                          total_duration=70.0, trim_overflow=True, ends=ends,
                          max_overhang=MAX_OVERHANG)
p = pls[0]
print(f"  SRT cua cau 0      : 5.00 -> 8.00 (dai 3.00s)")
print(f"  Giong TTS tu nhien : 25.00s")
print(f"  speed ap dung      : {p.speed:.3f}x  trimmed={p.trimmed}")
print(f"  Giong phat that su : {p.placed_start:.2f} -> {p.placed_end:.2f}")
overrun = p.placed_end - ends[0]
ok = overrun <= MAX_OVERHANG + 0.01
print(f"  >>> VUOT QUA SUB   : {overrun:+.2f}s  "
      + ("<-- OK, loi muon 22s da bi chan" if ok else "<-- LOI"))
if not ok:
    failures.append("long silence vuot tran")

if failures:
    raise SystemExit("FAIL: " + ", ".join(failures))
print("\nTIMING DIAGNOSTIC: PASS")
