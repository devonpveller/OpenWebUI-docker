<!-- Rendered 2026-09-12 through THIS branch's `scientific-paper` template and THIS branch's fidelity
     check, using the deployed LiteLLM path (qwen36-27b, temperature 0.2) - the same two steps a
     live run performs, in the same order. The synthesis is the recorded one from dry run a337520c (100 Hz auditory tones and VR motion sickness);
     nothing in it was edited. The Sources list and the footer are appended by renderResult and
     are not part of this render.

     The SAME skeleton over a literature question, with this template's names: Findings, and
     Findings by theme as the table. The sub-headings inside Findings are the template's own
     allowance for evidence that genuinely divides.

     What the pipeline recorded for this document:
       render fidelity : {"checked":57,"units":59,"unchecked":2,"stronger":2,"unsupported":6,"rewritten":1,"replaced":4}
       grounding diff  : numbers [] urls [] names []

     N and M are both counted on THIS file by `countUnits`; report-doc.test.ts asserts it. -->

# 100 Hz Bone-Conducted Sound Reduces Motion Sickness Symptoms via Otolith Resonance, with Theoretical but Unverified Applicability to VR-Induced Cybersickness

## Executive summary

The question concerns whether a 100 Hz pure tone, delivered via bone conduction, can mitigate motion sickness and whether that mechanism extends to virtual-reality (VR) cybersickness. A 2025 Nagoya University study (Gu, Kagawa, Kato, et al.) directly reports that a 1-minute pre-exposure to a 100 Hz tone at 80–85 dBZ (≈60.9–65.9 dBA) reduced motion sickness symptoms across three human motion scenarios, with the effect strongest in participants originally prone to sickness [Source 3, 10]. The mechanism—resonant vibration of otoconia in the utricle and saccule—was confirmed in ex vivo murine utricle explants and by surgical removal of otoliths [Source 5, 6, 7]. The extension to VR cybersickness is inferred from the shared visual-vestibular conflict mechanism but has not been tested in a controlled VR-headset experiment; the evidence for VR-specific efficacy is therefore theoretical and weakly supported [Source 1, 5, 6, 11, 12].

## Findings

### Otolith resonance as the causal mechanism

The 100 Hz frequency is identified as the resonant frequency of the otoconia (calcium carbonate crystals) in the otolith organs (utricle and saccule) of the inner ear. Sound at this frequency, delivered via bone conduction through headphones, physically vibrates the otoconia and provides supplementary vestibular input to the brain [Source 5, 6, 7]. In ex vivo murine utricle explant experiments, only 100 Hz at 75–85 dBZ for 5 minutes produced a vestibular response; no reaction was observed at 90 Hz, 250 Hz, 500 Hz, or 1000 Hz, and no reaction occurred below 75 dBZ [Source 7]. When the otoliths were surgically removed from the mouse tissue, the 100 Hz–induced activation disappeared, confirming that the otoliths (not the cochlea) were the target of the sound stimulation [Source 7]. The 100 Hz stimulation broadly activates the vestibular system, which is responsible for maintaining balance and spatial orientation, and reduces the autonomic dysregulation (excessive sympathetic activation) associated with motion sickness [Source 6, 7].

### Human efficacy across three motion scenarios

The human study enrolled 82 participants across three motion scenarios: a mechanical swing (pendulum motion), a 360-degree driving simulator, and a real vehicle driven on a course with curves [Source 5, 6, 7, 10]. Outcomes were measured objectively via posturography (center-of-gravity sway / envelope areas) and electrocardiography (heart rate variability, LF/HF ratio), and subjectively via the Motion Sickness Assessment Questionnaire (MSAQ) [Source 6, 7, 10]. The 1-minute pre-exposure to the 100 Hz tone improved posturography envelope areas (reduced sway) across all three motion scenarios [Source 10]. Driving-simulator-mediated sympathetic nerve activation (assessed by HRV) and vehicle-mediated MSAQ scores were both improved by the pure-tone exposure [Source 10]. The beneficial effect was stronger in participants who were originally prone to motion sickness; no significant difference was observed in participants less prone to sickness [Source 7].

The long-term (beyond 120 minutes) efficacy in humans is not established; the ≥120 min duration was demonstrated only in the mouse beam-balance model, and the human studies measured symptoms during a single motion exposure session. (unverified figure: 120) [Source 10]

In the mouse model, a 5-minute exposure to 85 dBZ at 100 Hz before shaking produced a long-lasting (≥120 min) alleviative effect on beam-balance test scores [Source 10]. The frequency and intensity specificity observed in the explant work (only 100 Hz at 75–85 dBZ elicited a response) is consistent with a resonant mechanism rather than a general auditory effect [Source 7].

Because the 100 Hz mechanism targets the vestibular (otolith) component of the sensory conflict, and VR cybersickness is driven by a visual-vestibular mismatch, the 100 Hz pre-exposure could theoretically reduce the vestibular "error signal" that contributes to VR-induced motion sickness, even though the visual stimulus in VR is the primary driver of the conflict. [Source 1, 5, 6, 11, 12]

VR motion sickness (cybersickness / VIMS) is attributed to a sensory conflict or mismatch between visual, vestibular, and proprioceptive signals integrated in the brainstem and cerebellum; in VR, the eyes perceive self-motion while the vestibular system detects stillness [Source 1, 11, 12, 15]. The driving-simulator scenario in the Nagoya study (visual motion + stationary body) is described as creating "the same mechanism that causes cybersickness in VR" [Source 5]. Because the 100 Hz mechanism targets the vestibular (otolith) component of the sensory conflict, and VR cybersickness is driven by a visual-vestibular mismatch, the 100 Hz pre-exposure could theoretically reduce the vestibular "error signal" that contributes to VR-induced motion sickness, even though the visual stimulus in VR is the primary driver of the conflict [Source 1, 5, 6, 11, 12]. However, the driving-simulator scenario is the closest proxy to VR cybersickness among the three tested conditions but is not identical to a full VR headset experience, which adds stereoscopic depth, head-tracked rendering, and vergence-accommodation conflict [Source 5, 11, 12].

### Delivery parameters and safety

The sound must be delivered equally to both ears; exposure to only one ear did not produce an effect. A design with two speakers placed 10 cm to the left and right of a car headrest was verified in the study [Source 7]. Recommended practical parameters from the study are: 100 Hz pure tone, 80–85 dBZ (≈60.9–65.9 dBA, roughly conversational volume), delivered binaurally (both ears) for 1 minute before the motion stimulus, in a quiet environment [Source 3, 4, 7, 10]. No effect on hearing was detected via distortion product otoacoustic emissions (DPOAE), and the 1-minute exposure at 80–85 dBZ is well below WHO occupational noise safety limits (100 Hz at 85 dB(A) is acceptable for up to 480 minutes) [Source 6, 7].

### Supporting evidence from related vestibular interventions

A galvanic vestibular stimulation (GVS) study in 10 participants showed that "Beneficial" GVS produced a 26% motion sickness reduction and "Detrimental" GVS produced a 56% increase (p = 0.0055), confirming the causal role of vestibular sensory conflict; the authors stated the findings "facilitate new methods and countermeasures for mitigating motion sickness during transportation and in virtual environments" [Source 2]. An EEG study of 14 healthy subjects in a VR environment found that with increasing visual-vestibular mismatch and subjective VIMS, the proportion of slow EEG waves (especially 1–10 Hz) increases, particularly in temporo-occipital regions, and information flow decreases in brain areas involved in vestibular signal processing [Source 1].

### Commercial and developmental context

Samsung reportedly incorporated the Nagoya University findings into its Galaxy Health ecosystem under a feature called "Hearapy," and independent developers (including RideCalm) built dedicated apps to deliver the 100 Hz tone [Source 5]. The researchers plan to further develop the technology for practical application in a variety of travel situations including air and sea travel, and the sound could be incorporated into existing devices such as hearing aids and other audio devices [Source 6, 8]. The study was registered under UMIN000022413 (2016/05/23–2023/04/19) and UMIN000053735 (2024/02/29–present) [Source 10].

## Findings by theme

| Theme | What the evidence shows | Strength of support | Source |
|---|---|---|---|
| Otolith resonance mechanism | 100 Hz is the resonant frequency of otoconia; bone-conducted sound at this frequency vibrates the otoliths and provides supplementary vestibular input; effect abolished by otolith removal | Directly demonstrated in murine explants and confirmed by surgical ablation | [Source 5, 6, 7] |
| Human motion-sickness reduction | 1-min pre-exposure at 80–85 dBZ reduced sway (posturography), sympathetic activation (HRV), and MSAQ scores across swing, driving-simulator, and vehicle conditions; effect stronger in motion-sickness-prone participants | 82 participants, three scenarios, objective and subjective endpoints; one sub-experiment had n = 10 | [Source 3, 5, 6, 7, 9, 10] |
| Mouse model validation | 5-min exposure at 85 dBZ / 100 Hz produced ≥120 min alleviative effect on beam-balance scores; frequency- and intensity-specific (no response at 90, 250, 500, 1000 Hz or <75 dBZ) | Ex vivo explant and in vivo animal data; figures partially unverified | [Source 7, 10] |
| VR cybersickness applicability | Shared visual-vestibular conflict mechanism; driving-simulator scenario described as same mechanism as VR cybersickness; theoretical inference that 100 Hz pre-exposure could attenuate the vestibular error signal | Inferred from mechanism overlap; no direct VR-headset experiment reported | [Source 1, 5, 6, 11, 12, 15] |
| Delivery parameters and safety | Binaural delivery required; 100 Hz, 80–85 dBZ, 1 min, quiet environment; no DPOAE-detected hearing effect; well below WHO occupational limits | Directly tested in the human study; safety figures partially unverified | [Source 3, 4, 6, 7, 10] |
| Related vestibular interventions | GVS confirmed causal role of vestibular conflict (26% reduction / 56% increase, p = 0.0055); EEG showed increased slow-wave activity with increasing VIMS | Small samples (n = 10, n = 14); supports mechanism but not 100 Hz specifically | [Source 1, 2] |
| Commercial and developmental trajectory | Samsung "Hearapy" feature; independent apps (RideCalm); planned extension to air/sea travel and hearing-aid integration | Reported by sources; no independent verification of efficacy in commercial products | [Source 5, 6, 8] |

## What the evidence does not settle

The extension of the 100 Hz effect to a full VR headset experience remains an inference. The driving-simulator condition in the Nagoya study is described as the closest proxy to VR cybersickness [Source 5], but a full VR headset adds stereoscopic depth, head-tracked rendering, and vergence-accommodation conflict that the simulator does not reproduce [Source 11, 12]. The evidence therefore supports a plausible mechanism-level argument but does not establish that the same magnitude of benefit would be observed under those additional visual conditions.

The long-term efficacy in humans is not established. The ≥120-minute alleviative duration was demonstrated only in the mouse beam-balance model [Source 10]; the human studies measured symptoms during a single motion exposure session. Whether the effect persists beyond a single session or requires repeated pre-exposure is not addressed in the provided sources.

The interaction between 100 Hz sound stimulation and other VR-specific countermeasures (high frame rates, reduced artificial locomotion, vignetting) is not addressed in any provided source [Source 11, 12]. Whether the 100 Hz effect is additive with other VR-specific countermeasures (e.g., high frame rates, reduced artificial locomotion, vignetting) is not addressed in any provided source. (unverified figure: 100) [Source 11, 12]

One sub-experiment in the human study had a sample size of only 10 participants, a limitation noted by community reviewers [Source 9]. When the otoliths were surgically removed from the mouse tissue, the 100 Hz–induced activation disappeared, confirming that the otoliths (not the cochlea) were the target of the sound stimulation. [Source 7]

## Limitations and open questions

The evidence is thin on the following points, none of which is resolved by the sources provided:

- No source in the provided set reports a controlled experiment that directly applied the 100 Hz tone inside a VR headset environment and measured cybersickness outcomes, so the specific quantitative effectiveness of 100 Hz sound for VR-induced motion sickness remains unverified.
- No source provides VR-specific developer guidance such as recommended integration APIs, timing relative to scene transitions, interaction with frame-rate or refresh-rate settings, or whether the tone should be continuous or pulsed during the VR session.
- The precise neurophysiological pathway by which supplementary otolith vibration resolves (or attenuates) the visual-vestibular prediction error in the brainstem/cerebellum during VR is not detailed in any provided source beyond the general statement that it "broadly activates the vestibular system."
- No source reports a head-to-head comparison of 100 Hz sound stimulation versus pharmacological antiemetics or versus other non-pharmacological VR countermeasures (e.g., GVS, fluid-filled glasses) in the same VR context.

A further run focused on controlled VR-headset trials of 100 Hz bone-conducted pre-exposure with cybersickness outcome measures, and on the neurophysiological pathway linking otolith vibration to visual-vestibular prediction-error resolution, would close this.
