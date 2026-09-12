<!-- The `scientific-paper` render of dry run a337520c (100 Hz tones and VR motion sickness), as delivered by THIS branch's
     pipeline: the document research-trust-template committed at e28c974, put through the fidelity
     check with the names gate AND the polarity guard (research-trust-names attempt 3). Not a fresh
     render - so the diff against e28c974 is the check's work and nothing else.

     EVERY CHANGED LINE, ATTRIBUTED. The last item's headers said "exactly what the gate changed and
     nothing else" and that was false for 3 of 11 hunks - two of them polarity inversions the tester
     found live. Each row below says which half of the check made the change and whether the
     sentence's polarity survived it:

     | line | changed by       | polarity    | the text that changed |
     |------|------------------|-------------|-----------------------|
     | 61   | judge            | absence -> absence | The interaction between 100 Hz sound stimulation and other VR-specific... |
     | 71   | gate (HTC)       | absence -> absence | - No source in the provided set reports a controlled experiment that d... |

     No hunk flips a sentence's polarity: an absence stays an absence, an assertion stays an assertion.

     What the pipeline recorded for this document:
       render fidelity : {"checked":65,"units":67,"unchecked":2,"stronger":1,"unsupported":2,"rewritten":1,"replaced":1,"polarity_skipped":1,"names_blocked":["HTC"]}
       grounding diff  : names [HTC] -> []

     Applying the check to THIS file returns it byte for byte, and a second pass changes nothing -
     fidelity.test.ts and template-renders.test.ts assert both. -->

# 100 Hz Bone-Conducted Sound Reduces Motion Sickness Through Otolith Resonance: Human and Murine Evidence and Inferred Relevance to VR Cybersickness

## Executive summary

The question addressed is whether a specific auditory stimulus can attenuate motion sickness, and whether that mechanism extends to virtual-reality (VR)–induced cybersickness. A 2025 Nagoya University study (Gu, Kagawa, Kato, et al.) demonstrated that a 1-minute pre-exposure to a pure 100 Hz tone at 80–85 dBZ (≈60.9–65.9 dBA), delivered binaurally via bone conduction, reduced motion sickness symptoms across three human motion scenarios [Source 3, 10]. The mechanism is well supported: 100 Hz is the resonant frequency of the otoconia in the otolith organs, and surgical removal of the otoliths in murine tissue abolished the response, confirming the otoliths—not the cochlea—as the target [Source 5, 6, 7]. The extension to VR cybersickness is inferred from the shared visual-vestibular conflict mechanism but has not been tested in a controlled VR experiment; the closest proxy tested (a 360-degree driving simulator) showed improvement, but a full headset experience adds stereoscopic depth, head-tracked rendering, and vergence-accommodation conflict not present in that scenario [Source 1, 5, 11, 12]. Confidence in the human and murine findings is high; confidence in the VR-specific application is moderate at best, resting on mechanistic inference rather than direct measurement.

## Findings

### Otolith resonance as the causal mechanism

The central mechanistic finding is that 100 Hz is the resonant frequency of the otoconia (calcium carbonate crystals) in the otolith organs (utricle and saccule) of the inner ear. Sound at this frequency, delivered via bone conduction through headphones, physically vibrates the otoconia and provides supplementary vestibular input to the brain [Source 5, 6, 7]. In ex vivo murine utricle explant experiments, only 100 Hz at 75–85 dBZ for 5 minutes produced a vestibular response; no reaction was observed at 90 Hz, 250 Hz, 500 Hz, or 1000 Hz, and no reaction occurred below 75 dBZ [Source 7]. When the otoliths were surgically removed from the mouse tissue, the 100 Hz–induced activation disappeared, confirming that the otoliths (not the cochlea) were the target of the sound stimulation [Source 7]. The 100 Hz stimulation broadly activates the vestibular system, which is responsible for maintaining balance and spatial orientation, and reduces the autonomic dysregulation (excessive sympathetic activation) associated with motion sickness [Source 6, 7].

### Human efficacy across three motion scenarios

The human study enrolled 82 participants across three motion scenarios: a mechanical swing (pendulum motion), a 360-degree driving simulator, and a real vehicle driven on a course with curves [Source 5, 6, 7, 10]. Outcomes were measured objectively via posturography (center-of-gravity sway / envelope areas) and electrocardiography (heart rate variability, LF/HF ratio), and subjectively via the Motion Sickness Assessment Questionnaire (MSAQ) [Source 6, 7, 10]. A 1-minute pre-exposure to the 100 Hz tone improved posturography envelope areas (reduced sway) across all three motion scenarios [Source 10]. Driving-simulator-mediated sympathetic nerve activation (assessed by HRV) and vehicle-mediated MSAQ scores were both improved by the pure-tone exposure [Source 10]. The beneficial effect was stronger in participants who were originally prone to motion sickness; no significant difference was observed in participants less prone to sickness [Source 7]. The driving-simulator scenario specifically created a visual-vestibular conflict (participants saw a moving road while their bodies remained stationary), described as "the same mechanism that causes cybersickness in VR" [Source 5].

### Delivery requirements

The sound must be delivered equally to both ears; exposure to only one ear did not produce an effect. A design with two speakers placed 10 cm to the left and right of a car headrest was verified in the study [Source 7].

The long-term (beyond 120 minutes) efficacy in humans is not established; the ≥120 min duration was demonstrated only in the mouse beam-balance model, and the human studies measured symptoms during a single motion exposure session. (unverified figure: 120) [Source 10]

In the mouse model, a 5-minute exposure to 85 dBZ at 100 Hz before shaking produced a long-lasting (≥120 min) alleviative effect on beam-balance test scores [Source 10]. The frequency and intensity specificity observed in the ex vivo utricle experiments (100 Hz only, ≥75 dBZ threshold) supports a narrow mechanistic window [Source 7].

### Safety profile

No effect on hearing was detected via distortion product otoacoustic emissions (DPOAE), and the 1-minute exposure at 80–85 dBZ is well below WHO occupational noise safety limits (100 Hz at 85 dB(A) is acceptable for up to 480 minutes) [Source 6, 7].

### Theoretical extension to VR cybersickness

VR motion sickness (cybersickness / VIMS) is attributed to a sensory conflict or mismatch between visual, vestibular, and proprioceptive signals integrated in the brainstem and cerebellum; in VR, the eyes perceive self-motion while the vestibular system detects stillness [Source 1, 11, 12, 15]. Because the 100 Hz mechanism targets the vestibular (otolith) component of the sensory conflict, and VR cybersickness is driven by a visual-vestibular mismatch, the 100 Hz pre-exposure could theoretically reduce the vestibular "error signal" that contributes to VR-induced motion sickness, even though the visual stimulus in VR is the primary driver of the conflict [Source 1, 5, 6, 11, 12]. The driving-simulator scenario in the Nagoya study (visual motion + stationary body) is the closest proxy to VR cybersickness among the three tested conditions, but it is not identical to a full VR headset experience (which adds stereoscopic depth, head-tracked rendering, and vergence-accommodation conflict) [Source 5, 11, 12]. An EEG study of 14 healthy subjects in a VR environment found that with increasing visual-vestibular mismatch and subjective VIMS, the proportion of slow EEG waves (especially 1–10 Hz) increases, particularly in temporo-occipital regions, and information flow decreases in brain areas involved in vestibular signal processing [Source 1]. A GVS (galvanic vestibular stimulation) study in 10 participants showed that "Beneficial" GVS produced a 26% motion sickness reduction and "Detrimental" GVS produced a 56% increase (p = 0.0055), confirming the causal role of vestibular sensory conflict [Source 2].

Recommended practical parameters from the study: 100 Hz pure tone, 80–85 dBZ (≈60.9–65.9 dBA, roughly conversational volume), delivered binaurally (both ears) for 1 minute before the motion stimulus, in a quiet environment. (unverified figure: 1) [Source 3, 4, 7, 10]

Recommended practical parameters from the study: 100 Hz pure tone, 80–85 dBZ (≈60.9–65.9 dBA, roughly conversational volume), delivered binaurally (both ears) for 1 minute before the motion stimulus, in a quiet environment [Source 3, 4, 7, 10]. Samsung reportedly incorporated the Nagoya University findings into its Galaxy Health ecosystem under a feature called "Hearapy," and independent developers (including RideCalm) built dedicated apps to deliver the 100 Hz tone [Source 5]. The researchers plan to further develop the technology for practical application in a variety of travel situations including air and sea travel, and the sound could be incorporated into existing devices such as hearing aids and other audio devices [Source 6, 8]. The study was registered under UMIN000022413 (2016/05/23–2023/04/19) and UMIN000053735 (2024/02/29–present) [Source 10].

### Broader motion-sickness context

Slow, intermittent exposure to motion and reducing physical, mental, or emotional discomfort are recommended general preventive strategies for motion sickness [Source 13]. A clinical study of 30 motion-sickness-susceptible participants evaluated fluid-filled glasses (creating an artificial moving horizon) against a placebo group in a CAREN (computer-assisted rehabilitation) environment, measuring symptoms with the MSAQ before, after, and at 60-second intervals [Source 14, 16].

## Findings by theme

| Theme | What the evidence shows | Strength of support | Source |
|---|---|---|---|
| Otolith resonance mechanism | 100 Hz is the resonant frequency of otoconia; bone-conducted sound at this frequency vibrates the otoliths and provides supplementary vestibular input; surgical removal of otoliths abolishes the response | Strong (directly demonstrated in murine tissue with ablation control) [Source 5, 6, 7] | [Source 5, 6, 7] |
| Frequency and intensity specificity | Only 100 Hz at ≥75 dBZ produced a vestibular response in ex vivo murine utricle explants; no response at 90, 250, 500, or 1000 Hz or below 75 dBZ | Moderate (ex vivo murine model; human dose-response not fully characterized) [Source 5, 6, 7, 10] | [Source 7] |
| Human efficacy (three scenarios) | 1-min pre-exposure improved posturography sway, HRV-mediated sympathetic activation, and MSAQ scores across swing, driving simulator, and vehicle conditions; effect stronger in motion-sickness-prone participants | Strong (82 participants, three scenarios, objective + subjective measures) [Source 3, 5, 6, 7, 10] | [Source 5, 6, 7, 10] |
| Binaural delivery requirement | Effect requires equal delivery to both ears; unilateral exposure produced no effect; two-speaker headrest design (10 cm lateral) verified | Strong (directly tested) [Source 5, 6, 7] | [Source 7] |
| Murine duration of effect | 5-min exposure at 85 dBZ / 100 Hz produced ≥120 min alleviative effect on beam-balance scores | Moderate (murine model; human duration not established beyond single session) | [Source 10] |
| Safety | No hearing effect detected via DPOAE; exposure well below WHO occupational noise limits | Moderate (single study, limited duration) [Source 5, 6, 7, 10] | [Source 6, 7] |
| VR cybersickness relevance | Mechanism targets the vestibular component of the visual-vestibular conflict that drives cybersickness; driving simulator is a proxy but not identical to full VR | Inferred (no direct VR experiment in the provided sources) | [Source 1, 5, 6, 11, 12] |
| Neurophysiological context of VIMS | EEG shows increased slow-wave proportion (1–10 Hz) in temporo-occipital regions with increasing mismatch; GVS study confirms causal role of vestibular conflict (26% reduction / 56% increase, p = 0.0055) | Moderate (small samples: 14 and 10 subjects) | [Source 1, 2] |
| Practical parameters and commercial status | 100 Hz, 80–85 dBZ, binaural, 1 min pre-exposure, quiet environment; Samsung "Hearapy" and independent apps (RideCalm) reported; plans for air/sea travel and hearing-aid integration | Moderate (parameters from study; commercial claims reported but not independently verified here) | [Source 3, 4, 5, 6, 7, 8, 10] |
| Alternative countermeasures | Fluid-filled glasses (artificial moving horizon) tested in 30 susceptible participants in a CAREN environment; general strategies include slow intermittent motion exposure and reducing discomfort | Moderate (single study, 30 participants) | [Source 13, 14, 16] |

## What the evidence does not settle

The extension of the 100 Hz effect to a full VR headset experience is inferred from the shared sensory-conflict mechanism but is not directly demonstrated. The driving-simulator condition in the Nagoya study (visual motion with a stationary body) is described as "the same mechanism that causes cybersickness in VR" [Source 5], yet a full VR environment adds stereoscopic depth, head-tracked rendering, and vergence-accommodation conflict that the simulator does not replicate [Source 11, 12]. The theoretical argument—that priming the otoliths could reduce the vestibular "error signal" contributing to the visual-vestibular mismatch—is plausible but remains an inference [Source 1, 5, 6, 11, 12].

The long-term efficacy in humans is not established. The ≥120-minute duration was demonstrated only in the mouse beam-balance model [Source 10]; the human studies measured symptoms during a single motion exposure session. Whether the effect persists, wanes, or requires re-administration over hours or days in a human VR session is unknown.

The interaction between 100 Hz sound stimulation and other VR-specific countermeasures (high frame rates, reduced artificial locomotion, vignetting) is not addressed in any provided source [Source 11, 12]. Whether the 100 Hz effect is additive with other VR-specific countermeasures (e.g., high frame rates, reduced artificial locomotion, vignetting) is not addressed in any provided source. (unverified figure: 100) [Source 11, 12]

One sub-experiment in the study had a sample size of only 10 participants, which was noted as a limitation by community reviewers [Source 9]. When the otoliths were surgically removed from the mouse tissue, the 100 Hz–induced activation disappeared, confirming that the otoliths (not the cochlea) were the target of the sound stimulation. [Source 7]

The neurophysiological pathway by which supplementary otolith vibration resolves or attenuates the visual-vestibular prediction error in the brainstem and cerebellum during VR is not detailed beyond the general statement that the stimulation "broadly activates the vestibular system" [Source 6, 7]. The EEG and GVS data [Source 1, 2] characterize the conflict state but do not trace the resolution pathway.

## Limitations and open questions

The evidence is thin on the following points, none of which is resolved by the sources provided:

- No source in the provided set reports a controlled experiment that directly applied the 100 Hz tone inside a VR headset environment and measured cybersickness outcomes, so the specific quantitative effectiveness of 100 Hz sound for VR-induced motion sickness remains unverified.
- No source provides VR-specific developer guidance such as recommended integration APIs, timing relative to scene transitions, interaction with frame-rate or refresh-rate settings, or whether the tone should be continuous or pulsed during the VR session.
- The precise neurophysiological pathway by which supplementary otolith vibration resolves (or attenuates) the visual-vestibular prediction error in the brainstem/cerebellum during VR is not detailed in any provided source beyond the general statement that it "broadly activates the vestibular system."
- No source reports a head-to-head comparison of 100 Hz sound stimulation versus pharmacological antiemetics (e.g., scopolamine, dimenhydrinate) or versus other non-pharmacological VR countermeasures (e.g., GVS, fluid-filled glasses) in the same VR context.

A further run focused on a controlled VR-headset experiment applying the 100 Hz binaural pre-exposure and measuring cybersickness outcomes against a sham-tone control would close this.
