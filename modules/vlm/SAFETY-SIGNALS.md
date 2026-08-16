# Safety signals — a design, grounded in published frameworks

Not implemented. This is the design for what we measure *after* the demo, and the
reasoning for why some obvious-sounding signals are in and others are out.

Written because "is the sidewalk blocked" is a thin slice of what makes someone
comfortable walking a street, and because guessing at the rest produces a scoring system
nobody can defend — least of all to a city that might adopt it.

---

## 1. Two frameworks that already did this work

### CPTED — Crime Prevention Through Environmental Design

Decades of research into which *environmental* features change perceived safety. Recent
work isolates **seven factors**: cleanliness, lighting, retail/business type, greening,
**people**, vehicles, and graffiti/fly-posting.

Two findings matter for us:

- **Lighting and cleanliness are the strongest positive contributors.** Visible physical
  disorder is the strongest negative.
- **"People" is a POSITIVE factor.** This is Jane Jacobs' "eyes on the street": other
  pedestrians present make a street feel *safer*. Which means the signal our stack
  already produces most reliably — person counts — is a positive one, and we have been
  treating it as neutral.

### Prospect–Refuge–Escape

The standard model for why a place feels threatening:

| term | meaning | direction |
|---|---|---|
| **prospect** | how far you can see; unobstructed sightlines | more is better |
| **refuge** | places another person could be concealed | more is worse |
| **escape** | routes out if something happens | more is better |

Fear is highest where there is **refuge for someone else and low prospect and escape for
you**. Concealment is the dominant cue, entrapment second.

This is the rigorous version of "somebody could hide in a corner and jump you" — and it
is a property of *geometry*, not of any person in frame, which is what makes it both
measurable and defensible.

---

## 2. Signal inventory

Status: ✅ have it · ⬜ buildable now · 🔻 needs new data/model · ⛔ will not build

| factor | our signal | source | status |
|---|---|---|---|
| **Lighting** | mean frame luminance, shadow/highlight spread | cv2 histogram, ~1 ms | ⬜ |
| | lit vs unlit at night per camera | existing enum + timestamps | ✅ |
| **People (positive)** | count, unique-per-minute, dwell time | detector + tracker | ✅ |
| **Vehicles** | volume, speed proxy | detector + tracker | ✅ |
| **Physical disorder** | graffiti, litter, damaged infrastructure — *property* | VLM prompt or classifier | ⬜ |
| **Retail / active frontage** | lit windows, open signage | VLM prompt, or OSM hours | ⬜ / 🔻 |
| **Greening** | canopy, planting | detector `potted plant`, Seattle canopy layer | ⬜ / 🔻 |
| **Prospect** | visible depth down the street | monocular depth or vanishing point | 🔻 |
| **Refuge / concealment** | recessed doorways, alley mouths, dumpsters, eye-level shrub, parked box trucks | detector classes + geometry; scores *places* | ⬜ |
| **Escape** | intersection density, exits per 100 m | street graph in `safe-walk/graph.py` | ⬜ cheap |
| **Infrastructure** | sidewalk presence, width, condition | SDOT `sidewalks.geojson`, joined | ✅ |
| **Collision history** | ped collisions, serious injuries, ROW-not-granted | SDOT, joined | ✅ |
| **Transit** | stop within N m, service running | GTFS | 🔻 |
| **Obstruction** | tent, scaffolding, truck, debris **on the walking path** | detector + geometry | ✅ scope |
| **Who the people are** | — | — | ⛔ §5 |

---

## 3. Positive biasing — bias *toward* documented good

The design change worth making. A street is not "risk minus nothing". A subtract-only
system ranks a well-lit, busy, well-maintained retail block identically to an unlit empty
one with no recorded incidents.

```
effective_cost = length × (1 + w · (risk − comfort))
   risk    ∈ [0,1]   what static_risk() already computes from SDOT
   comfort ∈ [0,1]   new; every term a documented fact
```

| comfort term | evidence | why it counts |
|---|---|---|
| other people present | detector count, now | CPTED's strongest social factor |
| street is lit | measured luminance + SDOT streetlight inventory | CPTED's strongest positive |
| sidewalk exists, good condition | SDOT `sidewalk_ratio`, `sidewalk_condition` | already in the graph |
| signalised crossing | SDOT signals/crosswalk layers | 74% of downtown ped collisions are at intersections |
| active frontage | open businesses, lit windows | retail factor; also more eyes |
| short blocks / high prospect | intersection density from the graph | prospect + escape |
| transit stop nearby | GTFS | a way out, and other waiting people |

Two rules that keep it honest:

1. **Comfort is capped below risk's weight.** A well-lit block with 32 recorded
   pedestrian collisions is still a block with 32 collisions. Positive evidence breaks
   ties; it does not overrule the collision record.
2. **Absence of evidence is not comfort.** A camera we cannot see is not a lit street.
   Missing data scores neutral, never good — otherwise unwatched blocks silently become
   "safe".

This is also the direct answer to the redlining problem raised at the start of this
project. A subtract-only system concentrates penalties wherever incidents are most
recorded — which tracks policing intensity as much as danger. A system that also credits
lighting, sidewalks, crossings and foot traffic rewards *investment*, and makes visible
where investment is absent. That is a finding a city can act on rather than a map that
tells residents their neighbourhood is bad.

---

## 4. Neighbour-camera corroboration

When one camera flags something, check its neighbours. Worth building, for reasons better
than the original intuition:

- **Corroboration.** One camera reporting something unusual is noise; three adjacent
  cameras agreeing is signal. Our own false-positive problem with the crop path
  (`RESULTS.md` §4b) is exactly what this catches.
- **Extent.** A closure has a length. Walking the neighbour graph tells you which
  segments are affected — which is what routing needs.
- **Cheap.** Cameras have lat/lon and `harness` owns camera→segment mapping.

Design note: trigger on the k nearest cameras **on the same street**, not merely within a
radius. A camera 80 m away on a parallel street sees a different world. `safe-walk`'s
street graph gives adjacency properly.

---

## 5. The line: we measure the environment, never who the people are

Several proposed signals were about classifying the people in frame — unusual behaviour,
people on the ground, apparent drug use, and counting tents as a ratio to scene
population. **These should not be built.** The reasons are practical before they are
ethical.

**They do not work.** A person sitting or lying on the ground, at 720×480 from 30 m, is
indistinguishable between tired, disabled, injured, waiting, sleeping rough, or having a
medical emergency. The model will be most confidently wrong about the people least able
to contest being flagged.

**A tent ratio is a poverty detector with a safety label.** The stated rationale — that
someone "who doesn't look impoverished" would feel unsafe — is the tell: it encodes one
group's discomfort at another group's presence. That is social sorting, not a hazard
measurement, and no amount of curve-fitting on the ratio changes what the underlying
variable is.

**It rebuilds the bias this project set out to avoid.** The first question asked here was
how to avoid redlining. Routing around encampments routes around poor neighbourhoods —
the same outcome, reached through a different variable.

**It ends the government pitch.** The plan is to face the city. No agency can adopt a
system that maps and routes around homeless people; that is a headline, not a
procurement. A sharp judge finds it in one question.

**It breaks our own stated differentiator.** `SPEC.md` §7.10 and `CLAUDE.md` say the VLM
never issues danger verdicts and never describes people beyond what they are physically
doing. We tell judges that restraint is what makes our claims defensible.

### What stays in scope, and covers the real concern

- **Obstruction is obstruction.** A tent that narrows or blocks the sidewalk is
  `narrowed`/`blocked`, scored exactly like scaffolding or a parked truck — geometry, no
  inference about who placed it. Already in the schema.
- **CPTED's factors carry the same signal legitimately.** The streets people have in mind
  are also, measurably: darker, emptier of ordinary foot traffic, worse maintained, with
  more concealment and fewer open businesses. Every one of those is a fact a resident
  could check and dispute. A tent count is not.
- **`emergency_response`** — visible emergency vehicles and flashing lights — stays. It
  is an observable object and a statement about response, not about a person.

**Rule: measure light, maintenance, activity, sightlines, infrastructure. Never posture,
appearance, or perceived intent.**

---

## 6. Build order

1. **Luminance from the frame** — ~1 ms of cv2; turns CPTED's strongest factor into a
   measured number instead of the VLM's `lighting` guess.
2. **Comfort term + positive biasing** — §3. Needs `synthesis` to own the weights.
3. **Escape/prospect from the street graph** — intersection density, block length. Pure
   arithmetic over data we already hold.
4. **Neighbour corroboration** — needs `harness` adjacency.
5. **Concealment scoring** — detector classes plus geometry; most speculative, do last.
6. **Depth for true prospect** — needs a model we do not have.

1–4 are buildable from what is already on the box.

## Sources

- CPTED seven-factor analysis of perceived safety in street environments —
  [Nagoya Institute of Technology](https://pure.nitech.ac.jp/en/publications/cpted-based-analysis-of-factors-influencing-perceived-safety-in-t/)
- CPTED strategies and pedestrian safety, Chennai —
  [Journal of Asian Architecture and Building Engineering](https://www.tandfonline.com/doi/full/10.1080/13467581.2025.2535563)
- Lighting, traffic lanes and vegetation on crossing safety perception —
  [PMC7699239](https://pmc.ncbi.nlm.nih.gov/articles/PMC7699239/)
- Walking environment modification and perceived safety —
  [PMC10800413](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10800413/)
- Fear of crime and prospect, refuge, escape — [Fisher & Nasar](https://journals.sagepub.com/doi/10.1177/0013916592241002),
  [Nasar & Jones, Landscapes of Fear](https://journals.sagepub.com/doi/10.1177/001391659702900301)
- Street lighting and greenery interplay in perceived safety —
  [Urban Design International](https://link.springer.com/article/10.1057/s41289-020-00134-6)
- Computational systematic social observation for environmental correlates of fear —
  [Crime Science](https://link.springer.com/article/10.1186/s40163-024-00242-6)
