# Community-reported hardware

`throttled` works at the CPU and chipset-register level. A laptop appearing in
this list is evidence from an individual user, not a compatibility guarantee
for every firmware, CPU, or kernel combination shipped under that model name.

Confirm the throttling cause with `sudo throttled --monitor`, keep conservative
settings, and report the CPU family/model/stepping and firmware version when
adding a new result.

The linked issues contain the underlying reports. Models are not added merely
because their CPU ID is recognized: the report must describe a useful result
from `throttled`. Reports involving unresolved thermal shutdowns or no observed
improvement are intentionally excluded.

## Lenovo

- ThinkPad T460 ([#192](https://github.com/erpalma/throttled/issues/192)),
  T470, T470s, T480, T480s, and T580
- ThinkPad T14 Gen 1, Gen 2
  ([#261](https://github.com/erpalma/throttled/issues/261)), and Gen 4
  ([#383](https://github.com/erpalma/throttled/issues/383))
- ThinkPad T14s Gen 2
  ([#332](https://github.com/erpalma/throttled/issues/332)) and Gen 4
  ([#346](https://github.com/erpalma/throttled/issues/346))
- ThinkPad T15 Gen 1 and T490
  ([#144](https://github.com/erpalma/throttled/issues/144))
- ThinkPad L390
  ([#203](https://github.com/erpalma/throttled/issues/203)), L480, L490,
  L590, and L14 Gen 2
  ([#291](https://github.com/erpalma/throttled/issues/291))
- ThinkPad X270
  ([#188](https://github.com/erpalma/throttled/issues/188)), X280, X380
  Yoga ([#90](https://github.com/erpalma/throttled/issues/90)), and X390
- ThinkPad X1 Carbon Gen 5, Gen 6, Gen 7
  ([#150](https://github.com/erpalma/throttled/issues/150)), Gen 8, and Gen 10
  ([#378](https://github.com/erpalma/throttled/issues/378))
- ThinkPad X1 Yoga Gen 3
  ([#72](https://github.com/erpalma/throttled/issues/72)) and X1 Yoga with
  i7-1280P ([#306](https://github.com/erpalma/throttled/issues/306))
- ThinkPad X1 Extreme Gen 2
  ([#157](https://github.com/erpalma/throttled/issues/157)), Gen 3, and Gen 4
  ([#318](https://github.com/erpalma/throttled/issues/318))
- ThinkPad Anniversary Edition 25
- ThinkPad E470, E480, E490, E580, and E590 with RX 550X
  ([#120](https://github.com/erpalma/throttled/issues/120))
- ThinkPad E14 Gen 2 and E16 Gen 1
  ([#353](https://github.com/erpalma/throttled/issues/353))
- ThinkPad P1 with i7-8750H
  ([#125](https://github.com/erpalma/throttled/issues/125)), P43s, P53,
  P14s Gen 1, P14s Gen 2
  ([#308](https://github.com/erpalma/throttled/pull/308)), and P15s Gen 1
- IdeaPad 720-15IKB
  ([#115](https://github.com/erpalma/throttled/issues/115)) and S145-15IWL
  ([#343](https://github.com/erpalma/throttled/issues/343))
- Yoga C930-13IKB
  ([#348](https://github.com/erpalma/throttled/issues/348))
- 51nb X210 ([#100](https://github.com/erpalma/throttled/issues/100))

## Dell

- XPS 9365, 9370, 9550, and 7390 2-in-1
- Latitude E5480
  ([#328](https://github.com/erpalma/throttled/issues/328)) and 7390 2-in-1
- Inspiron 15 5000 2-in-1
  ([#183](https://github.com/erpalma/throttled/issues/183)) and Inspiron 16
  Plus 7620
- Precision 7720, reported in combination with `thermald --adaptive`
  ([#380](https://github.com/erpalma/throttled/pull/380))

## HP

- ProBook 450 G5 and 470 G5
- ZBook Firefly 15 G7

## Microsoft

- Surface Book 2

## ASUS

- ASUS ZenBook UX430UNR, where a
  [static RAPL configuration](static-power-limits.md) can also be sufficient
  ([#134](https://github.com/erpalma/throttled/issues/134))
- ASUS ZenBook UX433FN
  ([#161](https://github.com/erpalma/throttled/issues/161))
- ASUS N551VW ([#178](https://github.com/erpalma/throttled/issues/178))
- ASUS ROG Strix GL504GS
  ([#223](https://github.com/erpalma/throttled/issues/223))
- ASUS VivoBook X513EPN/A512EP
  ([#388](https://github.com/erpalma/throttled/issues/388))

## Apple

- MacBookPro11,5
  ([#204](https://github.com/erpalma/throttled/issues/204))

## Other reports

- VAIO S11 VJS112C11T: undervolting improved performance, but the firmware
  ignored the requested temperature target
  ([#124](https://github.com/erpalma/throttled/issues/124))
- XMG NEO 15 / TUXEDO Stellaris 15 (2022): the reported package-power ceiling
  increased from 45 W to 60 W, with a remaining platform limit
  ([#333](https://github.com/erpalma/throttled/issues/333))

The original project discussion also includes reports for additional variants.
Please open an issue when a confirmed system is missing from this list.
