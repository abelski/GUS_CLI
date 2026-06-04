---
name: deep-research
description: Fan out web searches, cross-check sources, and synthesise a cited report
---
Conduct deep research on the following question:

$ARGUMENTS

Process:
1. Decompose the question into 4–8 specific sub-questions to search
2. Run web_search for each sub-question
3. Use web_fetch to read the most relevant pages in full
4. Cross-check facts across at least 3 sources
5. Identify any contradictions or gaps between sources
6. Synthesise findings into a structured report

Report format:
- **Summary** — 2–3 sentence answer to the original question
- **Key findings** — bullet points with citations [Source: URL]
- **Contradictions / uncertainty** — where sources disagree or data is missing
- **Sources** — numbered list of URLs consulted
