---
title: 'Fit route report on a 1080p screen'
type: 'bugfix'
created: '2026-06-03'
status: 'done'
route: 'one-shot'
---

# Fit route report on a 1080p screen

## Intent

**Problem:** In the generated route report, the Chart.js elevation profile renders very tall (its height scaled with screen width via the default aspect ratio), so the map and profile can't both be viewed at once on a 1080p screen.

**Approach:** Bump the map height +10% (420→462px) and pin the elevation profile to a fixed 180px by wrapping the canvas in a `position: relative` fixed-height container and setting Chart.js `maintainAspectRatio: false`.

## Suggested Review Order

- Map +10% and the new fixed-height, relative-positioned profile wrapper CSS.
  [`route.html.j2:13`](../../src/steeproute/templates/route.html.j2#L13)

- Canvas now wrapped in `#elevation-profile-wrap` instead of carrying a fixed `height` attribute.
  [`route.html.j2:65`](../../src/steeproute/templates/route.html.j2#L65)

- `maintainAspectRatio: false` so the chart fills the 180px wrapper rather than scaling with width.
  [`route.html.j2:149`](../../src/steeproute/templates/route.html.j2#L149)
