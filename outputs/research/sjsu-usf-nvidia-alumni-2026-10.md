# SJSU vs USF: alumni currently at NVIDIA, by field of study (LinkedIn Alumni tool)

> **Status: NOT YET RUN.** Every count below is still `pending`. Claude ran in a cloud container that has no access to your logged-in Chrome session, so it did not open LinkedIn. No number in this file was estimated or filled in.
> Run all queries on the same day and fill in the table. Steps are below.

## Results

Query date: `YYYY-MM-DD` (all rows must be from this same day)

| # | School | Field filter (exact LinkedIn label) | Total alumni | NVIDIA count | NVIDIA per 10k | Query time (local) | Screenshot |
|---|---|---|---|---|---|---|---|
| 1 | San Jose State University | pending (target: Computer Science) | pending | pending | pending | pending | `screenshots/sjsu-usf-nvidia/sjsu-01-computer-science.png` |
| 2 | San Jose State University | pending (target: Software Engineering) | pending | pending | pending | pending | `screenshots/sjsu-usf-nvidia/sjsu-02-software-engineering.png` |
| 3 | San Jose State University | pending (target: Computer Engineering) | pending | pending | pending | pending | `screenshots/sjsu-usf-nvidia/sjsu-03-computer-engineering.png` |
| 4 | San Jose State University | pending (target: Mathematics) | pending | pending | pending | pending | `screenshots/sjsu-usf-nvidia/sjsu-04-mathematics.png` |
| 5 | San Jose State University | pending (target: Applied Mathematics or closest computational/applied math option) | pending | pending | pending | pending | `screenshots/sjsu-usf-nvidia/sjsu-05-applied-mathematics.png` |
| 6 | University of San Francisco (usfca.edu) | pending (target: Computer Science) | pending | pending | pending | pending | `screenshots/sjsu-usf-nvidia/usf-01-computer-science.png` |
| 7 | University of San Francisco (usfca.edu) | pending (target: Data Science) | pending | pending | pending | pending | `screenshots/sjsu-usf-nvidia/usf-02-data-science.png` |
| 8 | University of San Francisco (usfca.edu) | pending (target: Mathematics) | pending | pending | pending | pending | `screenshots/sjsu-usf-nvidia/usf-03-mathematics.png` |

Rules for filling in:
- Copy each count exactly as LinkedIn displays it. If LinkedIn shows nothing for a count, write `not shown`. Never estimate.
- If LinkedIn shows a rounded or abbreviated number (for example "1.2K"), copy it exactly and note it under Notes. Do not expand it into a precise number.
- **NVIDIA per 10k** = NVIDIA count ÷ total alumni × 10,000, rounded to one decimal place. If either count is `not shown` or abbreviated, write `not computable`.
- Take a screenshot of each filter result at the moment you record it, showing the filter label, the total alumni count and the NVIDIA entry under "Where they work".

## Steps (manual page views only)

1. In your logged-in Chrome, open the LinkedIn school page for **San Jose State University**, then the **Alumni** tab.
2. Under **What they studied**, pick the option closest to the target field. Record its **exact** label (for example "Computer Science" or "Computer Software Engineering").
3. Record:
   - a) the total alumni count shown for that filter
   - b) the NVIDIA count under **Where they work**. If NVIDIA isn't in the top list, use the facet's search box or "+ Add" to find it.
4. Screenshot, record the time, then clear the filter before applying the next one. **Only one field filter should be active at a time.**
5. Repeat for **University of San Francisco**. Check that the school page is usfca.edu (San Francisco, CA), not University of South Florida.
6. Don't scrape, don't use the API, don't open or save individual profiles. Only record the aggregate counts LinkedIn displays.

## Notes

- Label mismatches (closest option chosen, and why):
- Rounded or abbreviated counts:
- Anything unusual (counts changing on reload, filter not available):

## Methodology and limitations

- **Current employer only.** The Alumni tool counts people whose *current* LinkedIn position is at NVIDIA. People who worked at NVIDIA in the past and have left are not counted, so this is a snapshot, not a historical hiring count.
- **Self-reported data.** School, field of study and employer all come from what members typed into their own profiles. Not everyone has a LinkedIn account, keeps it up to date, or lists a field of study. Field names are free text that LinkedIn maps into its filter options, so the mapping can be imperfect.
- **All degree levels.** A field-of-study filter includes everyone who lists that field at the school: bachelor's, master's, PhD, certificates and sometimes extension courses. SJSU has a large master's population in engineering fields, which can push its counts up relative to a bachelor's-only comparison.
- **Labels aren't equivalent across schools.** LinkedIn's closest option for a field may differ between SJSU and USF, so rows with different labels are not strictly comparable.
- **School size and history differ.** SJSU has far more alumni than USF and is in Silicon Valley near NVIDIA's Santa Clara headquarters. Per-10k rates adjust for alumni count but not for location, graduating era or degree mix.
- **LinkedIn counts can be approximate or rounded**, and can shift slightly between page loads. That's why all queries should run on the same day.
- **Not a probability.** These figures don't measure the chance of being admitted to either school or of being hired by NVIDIA. They show how many self-reported alumni currently list NVIDIA, and nothing more.
