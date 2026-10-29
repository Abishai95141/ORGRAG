# Telehealth platform: requirements v3

**Status:** In development · **Product owner:** Dana Okafor

## Background

Telehealth now accounts for 34% of Halcyon visits, up from 19% a year ago. The current vendor's platform does not support multi-party visits or integrated interpreters, and drop-off rates on Android are twice those on iOS.

## Goals for v3

1. Visit join success above 98% across devices, measured as patients who reach the clinician within 3 minutes of their start time.
2. Built-in three-way calls so interpreters and caregivers can join with a single link.
3. Waiting room with a live queue position and estimated wait.
4. Clinicians can share their screen to walk patients through lab results.

## Requirements

- Works in the browser with no app install; minimum supported versions are the last two releases of Chrome, Safari, Edge and Firefox.
- Video adapts down to 300 kbps; audio-only fallback starts automatically below that.
- All media is encrypted in transit; recordings are never made.
- Visit links expire 2 hours after the scheduled end time.
- Patients in states where the clinician is not licensed are blocked at scheduling, not at join.

## Out of scope

Remote patient monitoring devices and asynchronous (store-and-forward) visits are handled by separate projects.

## Success metrics

Track join success rate, median time-to-clinician, audio-only fallback rate, and the post-visit satisfaction score (target 4.6 out of 5).
