# Community-reported hardware

`throttled` works at the CPU and chipset-register level. A laptop appearing in
this list is evidence from an individual user, not a compatibility guarantee
for every firmware, CPU, or kernel combination shipped under that model name.

Confirm the throttling cause with `sudo throttled --monitor`, keep conservative
settings, and report the CPU family/model/stepping and firmware version when
adding a new result.

## Lenovo

- ThinkPad T470, T470s, T480, T480s, T580
- ThinkPad L480, L490, L590
- ThinkPad X280, X390
- ThinkPad X1 Carbon Gen 5, Gen 6, and Gen 8
- ThinkPad X1 Extreme Gen 4
- ThinkPad Anniversary Edition 25
- ThinkPad E480, E580, E590 with RX 550X, and E14 Gen 2
- ThinkPad P43s, P53, P14s Gen 1, P15s Gen 1
- ThinkPad T14 Gen 1 and T15 Gen 1

## Dell

- XPS 9365, 9370, 9550, and 7390 2-in-1
- Latitude 7390 2-in-1
- Inspiron 16 Plus 7620
- Precision 7720 (reported with `thermald`)

## HP

- ProBook 450 G5 and 470 G5
- ZBook Firefly 15 G7

## Microsoft

- Surface Book 2

## Other reports

- ASUS ZenBook UX430UNR, where a
  [static RAPL configuration](static-power-limits.md) can be sufficient

The original project discussion also includes reports for additional variants.
Please open an issue when a confirmed system is missing from this list.
