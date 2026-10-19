"""R67-3 验收（续）：第一层候选池过滤 + 10-16 不被误杀。"""
from __future__ import annotations

import logging
import sys
from datetime import date

sys.path.insert(0, "src")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s",
                    stream=sys.stdout)

from emotion_core.services.strategy import runner, universe  # noqa: E402

for d in (date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)):
    have = runner._has_tradable_bar(d, "600825")
    print(f"_has_tradable_bar({d}, 600825) = {have}")

codes13 = universe.build_universe(date(2026, 10, 13))
print(f"\nbuild_universe(2026-10-13) -> {len(codes13)} codes; 600825 in it: {'600825' in codes13}")

ok = universe.tradable_codes(date(2026, 10, 13))
print(f"tradable_codes(2026-10-13): {len(ok)} codes; 600825 in it: {'600825' in ok}")

# 保序过滤语义
sample = ["600825", "002343", "000530"]
print(f"\ndrop_untradable({sample}, 2026-10-13) = {universe.drop_untradable(sample, date(2026, 10, 13))}")
print(f"drop_untradable({sample}, 2026-10-16) = {universe.drop_untradable(sample, date(2026, 10, 16))}")

assert runner._has_tradable_bar(date(2026, 10, 13), "600825") is False
assert runner._has_tradable_bar(date(2026, 10, 16), "600825") is True
assert "600825" not in codes13
assert universe.drop_untradable(sample, date(2026, 10, 16)) == sample
print("\nR67-3 LAYER1/LAYER2 ACCEPT OK")
