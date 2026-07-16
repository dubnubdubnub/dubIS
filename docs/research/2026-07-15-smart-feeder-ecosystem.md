# Smart-Feeder Ecosystem Survey (for the ESP32-C6 wireless feeder + AprilTag concept)

**Date:** 2026-07-15 · **Method:** deep-research harness — 5 search angles, 17 primary sources fetched, 84 claims extracted, top 25 adversarially verified (3 independent refuters each): 21 confirmed, 4 refuted. Feeds the Phase 3 (PnP/feeder layer) design; see `docs/plans/2026-07-15-platform-architecture-design.md`.

## Executive summary

The ecosystem splits into two tiers:

1. **Opulo LumenPnP / Photon** — the only design with true per-feeder intelligence: Photon firmware on each feeder, RS-485 addressing, 1-Wire EEPROM **slot** identification, and a first-class `PhotonFeeder` class **merged into mainline OpenPnP** (org.openpnp.machine.photon) with dynamic bus discovery.
2. **Host-driven "dumb bank" designs** (0816 feeders, Esp32FeederController, Yamaha CL adaptations) — all integration logic lives host-side in OpenPnP's `ReferenceAutoFeeder`/`ReferenceSlotAutoFeeder` actuator abstraction; feeders addressed by manually configured index numbers over serial G-code M-codes.

OpenPnP already provides the key architectural pattern the ESP32-C6 design needs — **decoupling feeder identity from physical slot** (PhotonFeeder's `hardwareId` vs `slotAddress`; ReferenceSlotAutoFeeder's bank/slot model with per-slot pick locations + per-feeder offsets). But every existing slot-ID scheme is **electrical (1-Wire EEPROM) or manual — none is vision-based**, and **no verified prior art exists for per-feeder wireless (WiFi/BLE) nodes integrated with OpenPnP**, nor for AprilTag/ArUco feeder-slot identification.

## Verified findings (all 3-0 unless noted)

1. **Opulo/Photon is the reference prior art.** Open hardware (opulo-inc/feeder) + open Photon firmware; `PhotonFeeder` in mainline OpenPnP auto-scans the RS-485 bus (via Marlin M485 pass-through on the LumenPnP motherboard) and auto-populates the Feeders tab. No custom host glue needed. [opulo-inc/feeder; openpnp wiki Photon-Feeder; PhotonFeeder javadoc]
2. **Photon slot-ID is electrical, not vision:** each machine slot carries a 1-Wire EEPROM programmed with the slot address; the feeder reads it when mounted. Host-side: `hardwareId: String` (persistent identity) + `slotAddress: Integer 1-254`, `findByHardwareId()`/`findBySlotAddress()`, dynamic `findAllFeeders()` bus scan. **This is the direct architectural precedent for the AprilTag goal — same identity/slot split, different sensing.**
3. **0816 family = host-driven, manual index addressing.** mgineer85/0816-feeder-firmware: Arduino, custom M-codes (M600 advance, M601 retract-post-pick, M602 feeder-OK, M603 servo angle) over 115200-baud serial via GcodeDriver actuators; feeder selected by `N` index; no ID scheme of any kind.
4. **The only verified ESP32 precedent (atanisoft/Esp32FeederController) is a *wired bank controller*,** not per-feeder wireless: drives multiple 0816-style feeders over four-wire cables; feeder identity = zero-indexed slot number in the OpenPnP actuator value. (The claim that it exposes a clean WiFi/TCP:8989 integration story was refuted 0-3 — treat wireless-transport details as unconfirmed.)
5. **Yamaha CL adaptation (crono2250):** Mega2560 over USB serial, M610 bank power / M600 N<pin>, GPIO pin addressing 0-35, zero custom Java — stock GcodeAsyncDriver + ReferenceAutoFeeders.
6. **OpenPnP's ReferenceSlotAutoFeeder already models banks/slots:** any feeder in a bank can occupy any slot; each slot has a calibrate-once pick location; each feeder carries its part assignment and offsets; **which feeder is in which slot is a manual dropdown choice** — no automatic detection outside Photon.
7. **Gap analysis (medium confidence — argument from absence):** for the ESP32-C6 + powered-dock + AprilTag design you would have to build ALL of:
   - **Feeder firmware** from scratch or heavily adapted (Photon firmware assumes RS-485 + 1-Wire; 0816 assumes wired serial).
   - **OpenPnP-side integration** — either a new feeder class modeled on PhotonFeeder's hardwareId/slotAddress split (closest analogue) or a bridge presenting the wireless dock as a GcodeDriver/actuator target — **plus new up-camera vision code for tag-based slot discovery, since none exists**.
   - **The entire dubIS-facing layer** (reel/feeder identity registry, HTTP inventory sync) — no surveyed project addresses inventory integration at all.

## Refuted claims (do not rely on)

- "Photon is proprietary and Marlin-M485-only on the machine side" (0-3).
- "Esp32FeederController = WiFi/TCP:8989 GcodeDriver integration, no feeder class" (0-3 as stated).
- "Max Feeder QR-code part-ID is vision prior art" (1-2).
- "Yamaha CL adaptation bypasses Yamaha's native feeder electronics entirely" (1-2).

## Open questions (for Phase 3 spec / prototyping)

1. Does the Photon protocol have any transport abstraction seam in OpenPnP's `photon.protocol` package that a WiFi/ESP32-C6 dock could implement, or is a new feeder class cleaner?
2. Real-world latency/reliability of WiFi (vs ESP32-C6's 802.15.4/Thread) for feed-advance inside a pick cycle — does OpenPnP's `feed()` tolerate hundreds of ms of async round-trip without stalling throughput?
3. Can OpenPnP's existing fiducial/CvPipeline vision locate AprilTag/ArUco, or does tag detection need a new pipeline stage/library dependency in OpenPnP (Java)?
4. What does index.machines / commercial auto-feeder adaptation (CM402/Fuji/Siplace) actually provide? (Survey produced no verified claims for that segment — uncovered corner.)

## Design implications for dubIS Phase 3

- Model the dubIS feeder entity on the **hardwareId (feeder/reel identity) vs slotAddress (physical position)** split — it's proven in Photon and matches our part-registry philosophy (stable identity + reassignable location).
- The dock/slot AprilTag maps naturally to `slotAddress`; the feeder's own tag/MAC to `hardwareId`; dubIS holds the hardwareId ↔ reel ↔ part-uid mapping and serves it over `/v1/pnp/feeders`.
- Plan for a **custom OpenPnP feeder class** (Java) as the likely integration point; budget for an OpenPnP-side vision addition for tag detection (possibly upstreamable).
- Powered-dock electrical design rationale worth reading before HW: opulo-inc/feeder `DESIGN_DECISIONS.md` (why RS-485 over CAN/I2C/wireless; spring-pin slot interface: 4 pins RS-485+power, 1 pin 1-Wire EEPROM).

## Caveats

High-confidence findings rest on primary sources (repos, wiki, javadoc) with unanimous verification. The gap analysis is partly argument-from-absence — unpublished community wireless projects may exist (OpenPnP Google Group / Discord not exhaustively covered); index.machines segment effectively uncovered; wireless-pitfall data (latency/power/motion-sync) produced no surviving claims, so that question is answered only negatively. PhotonFeeder internals evolve — re-check OpenPnP `develop` before writing Java against it.
