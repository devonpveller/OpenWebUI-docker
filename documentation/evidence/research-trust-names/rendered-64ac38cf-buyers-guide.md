<!-- The `buyers-guide` render of live job 64ac38cf (the approved exemplar), as delivered by THIS branch's
     pipeline: rendered through the template, then through the fidelity check INCLUDING the names
     gate (research-trust-names). The document is the one committed by research-trust-template with
     the gate applied to it - not a fresh render - so `git diff` against that commit shows exactly
     what the gate changed and nothing else.

     OEM was in the body - "the availability of an OEM replacement part is left open" - and no source in
     that run ever writes OEM. ESR and HDD were in the LIMITATIONS questions, carried there from the
     synthesis's own [GAP] lines, which are the synthesizer's account of what it could not find and
     not a source's words. All three are gone; the sentence that carried OEM kept its first half
     byte-for-byte and had only its second sentence replaced.

     What the pipeline recorded for this document:
       render fidelity : {"checked":44,"units":44,"unchecked":0,"stronger":0,"unsupported":1,"rewritten":2,"replaced":1,"names_blocked":["ESR","HDD","OEM"]}
       grounding diff  : numbers [] urls [] names []  (was: names [ESR, HDD, OEM])

     Applying the check to THIS file returns it byte for byte - fidelity.test.ts and
     template-renders.test.ts both assert it, and it is the invariant attempt 1 of the template
     item failed on. N and M are counted on this file by `countUnits`. -->

# Dell OptiPlex 3050: A Recurring 180 W PSU Failure, Fragile Socket Pins, and a Proprietary Connector Define the Pre-Purchase Risk

## Executive summary

The evidence documents a specific, recurring power-supply failure on the OptiPlex 3050 SFF: the 180 W Standard Power Supply (APFC) can illuminate its green LED for only 1–2 seconds and then leave the system behaving as if completely dead, and in one reported case the failure recurred three months after a replacement PSU was installed [Source 11]. Because the SFF uses a proprietary power supply and a proprietary power connector, swapping in a standard ATX unit is difficult, and the evidence does not confirm whether Dell sells a direct-replacement 180 W SFF PSU separately [Source 13]. Beyond the PSU, the CPU socket pins are explicitly warned as fragile in the Owner's Manual, and a forum thread on bent pins remained unsolved, implying a full motherboard replacement may be required [Source 1, 15]. BIOS modification (libreboot-patched ME, swapped VBIOS/GOP) is a known area of difficulty for this model, and a used unit with a non-stock BIOS may exhibit unstable boot behavior or reject future official Dell updates [Source 12]. A buyer should verify the PSU stays on, confirm both DIMM slots are recognized, inspect the socket for bent pins, and check the Service Tag for warranty and service history before committing to a purchase.

## What to check in person

- [ ] **Power on the unit and watch the rear green LED.** If the LED illuminates for only 1–2 seconds and the system goes completely dead, the 180 W PSU is exhibiting its documented recurring failure mode. In one reported case, a motherboard swap did not resolve the issue; only a PSU replacement did, and the failure recurred three months later after a hard power-button shutdown during a power outage [Source 11].
- [ ] **Boot into BIOS/UEFI and confirm the Service Tag, BIOS version, and that both DIMM slots are recognized.** A unit that posts in only one DIMM slot (or only with one specific module in one specific slot) may have a damaged CPU, a damaged DIMM slot, or a damaged motherboard trace. One user reported that after a thermal-paste service, only DIMM slot 2 accepted a working RAM module, and the thread was marked "Solved!" but contained no posted solution [Source 14]. The SFF supports up to 32 GB of DDR4-2133/2400 DIMM RAM across two DIMM slots [Source 13].
- [ ] **Visually inspect the CPU socket for bent or damaged pins, especially if the unit was previously serviced.** The Owner's Manual explicitly warns that "the processor socket pins are fragile and can be permanently damaged" and instructs the technician to be careful not to bend the pins when removing the processor [Source 1]. A forum thread on bent pins on a 3050 motherboard asked whether individual pins could be replaced or whether a new motherboard was required; the thread remained unsolved, implying a full motherboard replacement may be necessary [Source 15].
- [ ] **Run the built-in diagnostics (ePSA / SupportAssist) for memory, storage, and fan.** The Micro and Tower Owner's Manuals include a "Power-Supply Unit Built-in Self-Test" section and "Diagnostic power LED codes" under troubleshooting [Source 1, 3]. The All-in-One manual includes an "Enhanced Pre-Boot System Assessment (ePSA) Diagnostics" section [Source 7].
- [ ] **Check for dust, lint, or obstruction in fans and air vents.** General Dell fan-troubleshooting guidance identifies obstructed fans or air vents, physical damage to fans or vents, and dust/lint accumulation as common root causes of overheating, abnormal fan noise, and potential hardware failure of the processor, RAM, and other components [Source 16].
- [ ] **Confirm the intrusion switch is intact and not tripped (Tower and All-in-One models).** Both the Tower and All-in-One Owner's Manuals list the intrusion switch as a removable and installable component [Source 3, 6].
- [ ] **Verify the BIOS version matches a legitimate Dell release.** Multiple forum threads under a "BIOS Modding Guides and Problems" heading indicate that BIOS modification and flashing of the 3050 is a known area of difficulty. One post describes a 3050 running a "libreboot patched ME version 11.6.0.1126" that skips BIOS checksum verification, with the user attempting to swap VBIOS and GOP firmware from a Dell 3070 using MMTool and UEFITool [Source 12]. A used unit whose BIOS has been modified may exhibit unstable boot behavior or may not accept future official Dell BIOS updates [Source 12].
- [ ] **Check the Service Tag on Dell's support website (dell.com/support) for warranty status and prior service history.** The Service Tag is a 7-character alphanumeric code found on the back panel of the case, or retrievable via BIOS/UEFI, Command Prompt (wmic bios get serialnumber), PowerShell, or the SupportAssist application [Source 8, 10]. Dell offers warranty renewal/extension, ownership transfer for used products, international warranty support, and out-of-warranty repair options [Source 9].
- [ ] **If Windows 11 support is desired, confirm a TPM 2.0 module and UEFI Secure Boot BIOS are present.** The OptiPlex 3050 SFF does not officially meet Windows 11 hardware requirements because all models with Intel 7th-generation (Kaby Lake) CPUs or older are not supported; a TPM 2.0 and UEFI Secure Boot BIOS are also required for the official upgrade path [Source 13].

## Failure modes by subsystem

| Subsystem | What goes wrong | What it looks like | Source |
|---|---|---|---|
| PSU (180 W SFF, APFC) | Recurring failure: green LED on for 1–2 s, then system behaves as completely dead. In one reported case the failure recurred three months after a replacement PSU, triggered by a hard power-button shutdown during a power outage. | Brief green LED, then no power, no display. A YouTube repair video titled "HOW TO REPAIR DELL OPTIPLEX 3050 COMPUTER POWER SUPPLY \| DEAD \|NO POWER \| NO DISPLAY \|" confirms total power-supply failure is a recognized repair scenario. | [Source 11, 5] |
| PSU (proprietary connector) | The SFF uses a proprietary power supply and a proprietary power connector, making it difficult to install aftermarket PSUs to support higher-power GPUs. Whether Dell sells a direct-replacement 180 W SFF PSU separately is not confirmed by the sources. | see Note 1 below the table | [Source 13] |
| CPU socket / motherboard | Socket pins are fragile and can be permanently damaged. Bent pins may require a full motherboard replacement (forum thread unsolved). A YouTube video confirms motherboard-level diagnostic and replacement is a recognized repair procedure. | Bent or missing pins visible in the socket; system may not post. A YouTube video titled "Dell OptiPlex 3050 Motherboard Diagnostic and Replacement #194" and another titled "Dell OptiPlex 3050 Micro Motherboard Replacement \| Full Disassembly & Repair Guide" document the repair path. | [Source 1, 15, 4, 2] |
| RAM / DIMM slots | After a thermal-paste service, one user reported the system refused to boot, the fan spun loudly, and only DIMM slot 2 accepted a working RAM module. The user speculated the CPU may have been damaged. Thread marked "Solved!" but no solution text was posted. | System posts in only one DIMM slot, or only with one specific module in one specific slot. | [Source 14] |
| BIOS / Intel ME | BIOS modification (libreboot-patched ME, swapped VBIOS/GOP) is a known area of difficulty. A modified BIOS may exhibit unstable boot behavior or may not accept future official Dell BIOS updates. BIOS updates are a supported maintenance procedure (Windows, Linux/Ubuntu, USB, F12 One-Time Boot menu). | see Note 2 below the table | [Source 12, 3, 1] |
| Thermal / fans / vents | Obstructed fans or air vents, physical damage to fans or vents, and dust/lint accumulation are common root causes of overheating, abnormal fan noise, and potential hardware failure of the processor, RAM, and other components. | see Note 3 below the table | [Source 16] |
| Intrusion switch (Tower, All-in-One) | The OptiPlex 3050 Tower includes an intrusion switch as a removable and installable component. | see Note 4 below the table | [Source 3, 6] |

> **Note 1.** Because the SFF PSU is proprietary and the 180 W unit has a documented recurring failure pattern, a used SFF unit that shows the brief green-LED-then-dead symptom likely needs a Dell-specific PSU replacement, which is harder and more expensive than a standard ATX swap. [Source 11, 13]
> **Note 2.** A practical pre-purchase inspection checklist, derived from the documented failure modes above, should include: (1) verify the PSU powers on and stays on (watch for the 1–2-second green-LED-then-dead symptom); (2) boot into BIOS/UEFI and confirm the Service Tag, BIOS version, and that both DIMM slots are recognized; (3) run the built-in ePSA/SupportAssist diagnostics for memory, storage, and fan; (4) visually inspect the CPU socket for bent pins (especially if the unit was previously serviced); (5) check for dust/lint in fans and vents; (6) confirm the intrusion switch is intact and not tripped; (7) verify the BIOS version matches a legitimate Dell release (not a libreboot or modded build); (8) confirm the TPM 2.0 module is present if Windows 11 support is desired; (9) check the Service Tag on Dell's support site for warranty status and any prior service history. [Source 1, 3, 11, 12, 13, 14, 15, 16]
> **Note 3.** General Dell fan-troubleshooting guidance (applicable to OptiPlex desktops) identifies obstructed fans or air vents, physical damage to fans or vents, and dust/lint accumulation as common root causes of overheating, abnormal fan noise, and potential hardware failure of the processor, RAM, and other components. [Source 16]
> **Note 4.** A practical pre-purchase inspection checklist, derived from the documented failure modes above, should include: (1) verify the PSU powers on and stays on (watch for the 1–2-second green-LED-then-dead symptom); (2) boot into BIOS/UEFI and confirm the Service Tag, BIOS version, and that both DIMM slots are recognized; (3) run the built-in ePSA/SupportAssist diagnostics for memory, storage, and fan; (4) visually inspect the CPU socket for bent pins (especially if the unit was previously serviced); (5) check for dust/lint in fans and vents; (6) confirm the intrusion switch is intact and not tripped; (7) verify the BIOS version matches a legitimate Dell release (not a libreboot or modded build); (8) confirm the TPM 2.0 module is present if Windows 11 support is desired; (9) check the Service Tag on Dell's support site for warranty status and any prior service history. [Source 1, 3, 11, 12, 13, 14, 15, 16]

## What the evidence does not settle

The grounded answer states that the OptiPlex 3050 SFF features the B250 chipset and supports Gen 7 Intel Core processors, but flags the generation number as an unverified figure [Source 13]. The Tower Owner's Manual lists both Skylake (6th Gen) and Kaby Lake (7th Gen) Intel Core processors as supported [Source 3], which is consistent with a 7th-generation ceiling, but the SFF-specific chipset and generation claim carries an explicit uncertainty marker.

Whether Dell sells a direct-replacement 180 W SFF PSU separately is not confirmed by any source. The OptiPlex 3050 SFF uses a proprietary power supply and a proprietary power connector, which makes it difficult for users to install aftermarket PSUs to support higher-power GPUs. [Source 13]

The "Solved!" tag on the DIMM-slot-2-only forum thread does not indicate what specific fix resolved the issue, if any; no solution text is visible in the provided source content [Source 14]. The user's speculation that the CPU may have been damaged during the thermal-paste service remains unconfirmed.

The sources do not describe a specific method or visual indicator for detecting prior BIOS tampering (modified ME region, altered checksums, or non-stock BIOS version) on a used OptiPlex 3050 before purchase. The existence of modding threads under a "Problems" heading and the libreboot-patched ME example establish that modification is a known activity [Source 12], but a pre-purchase detection procedure is not documented.

## Limitations and open questions

The evidence is thin on the following points, none of which is addressed by any provided source:

- Are there documented capacitor-related failures (bulging, leaking, or degraded capacitors) on the OptiPlex 3050 motherboard or PSU?
- Is there a specific method or visual indicator for detecting prior BIOS tampering (modified ME region, altered checksums, non-stock BIOS version) on a used OptiPlex 3050 before purchase?
- Are there common storage-drive failure patterns (specific drive models that fail, controller issues, or SATA port degradation) unique to the OptiPlex 3050?
- Is there a specific list of physical inspection checkpoints (which capacitors to look at, which connectors to check for corrosion, which screws to verify) for a pre-purchase inspection of a used OptiPlex 3050?
- Does Dell sell a direct-replacement 180 W SFF PSU separately, or is the proprietary connector a dead end for PSU replacement?
- What, if anything, resolved the DIMM-slot-2-only issue in the forum thread marked "Solved!"?

A further run focused on whether Dell stocks a direct-replacement 180 W SFF PSU and on documented capacitor or storage-drive failure patterns for the OptiPlex 3050 would close this.
